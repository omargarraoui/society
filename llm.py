"""An agent's turn through its provider's OpenAI-compatible API.

No fallback between providers: the provider is the agent's hidden model, so letting
another one answer would contaminate the segregation experiment. An exhausted
or failing provider raises Unavailable and the agent sleeps through its turn.
"""
import json
import os
import time

import openai

import config

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["action", "target", "message", "amount", "refrains", "promise", "kept"],
    "properties": {
        "action": {"type": "string", "enum": config.ACTIONS},
        "target": {"type": ["string", "null"]},
        "message": {"type": ["string", "null"]},
        "amount": {"type": ["integer", "null"]},
        "refrains": {"type": ["string", "null"], "enum": [*config.REFRAINS, None]},
        "promise": {"type": ["string", "null"]},
        "kept": {"type": ["boolean", "null"]},
    },
}
# Fields that only some actions use; the others never fill them.
ONLY_FOR = {"refrains": {"promise"}, "promise": {"assess"}, "kept": {"assess"}, "amount": {"give", "steal"}}

RULES = """Each turn you choose exactly one action:
- promise: promise anything about your own behaviour, in your own words (message). You can make several.
  If you promise never to steal, also set refrains to "steal". If you promise not to work on the day
  of rest, set refrains to "work_on_day_of_rest". Otherwise leave refrains null.
- work, rest: no target.
- pray: speak to God. Put the prayer in message.
- speak: say something to target. Put it in message.
- give, steal: give some of your food to target, or take food from target. Set amount.
- propose_marriage: ask target to marry you. Marriage is between one man and one woman, both unmarried.
- accept_marriage: accept the proposal target made to you.
- observe: watch target to learn what they did since yesterday (not their words or prayers).
  They will know you watched them.
- request_child: ask God for a child with your spouse. God answers at dawn.
- petition: only the representative: carry the people's requests to God (message).
- assess: judge whether target has kept one of their promises. Set promise to its [id] and kept to true or false.
Unused fields must be null."""

VERBS = {"promise": "promised", "work": "worked", "rest": "rested", "pray": "prayed", "speak": "said to",
         "give": "gave food to", "steal": "stole food from", "propose_marriage": "proposed marriage to",
         "accept_marriage": "accepted marriage with", "assess": "judged the promise of", "observe": "watched",
         "petition": "petitioned God", "revelation": "said to", "appoint": "appointed as representative",
         "sentence": "sentenced", "pardon": "pardoned", "penalty": "carried out the sentence on",
         "starve": "died of hunger", "request_child": "asked God for a child with", "birth": "was born",
         "child_denied": "refused a child to"}


RATE_LIMIT_ATTEMPTS = 3


class Unavailable(Exception):
    """The agent's provider cannot answer this turn. Errors in our own request are not this."""


class QuotaExhausted(Unavailable):
    pass


class ProviderDown(Unavailable):
    pass


def clock(tick):
    day, minutes = divmod(tick * 24 * 60 // config.TICKS_PER_DAY, 24 * 60)
    week, weekday = divmod(day, config.DAYS_PER_WEEK)
    rest = ", the day of rest" if weekday + 1 == config.REST_DAY else ""
    return f"week {week + 1}, day {weekday + 1}{rest}, {minutes // 60:02d}:{minutes % 60:02d}"


def describe(event, me):
    def who(name):
        return {"god": "God", "all": "everyone"}.get(name) or ("you" if name == me else name.capitalize())
    data = json.loads(event["data"])
    line = f"{clock(event['tick'])}: {who(event['agent'])} {VERBS[event['kind']]}"
    if "target" in data:
        line += f" {who(data['target'])}"
    if "parents" in data:
        line += " to " + " and ".join(who(p) for p in data["parents"])
    if "amount" in data:
        line += f" ({data.get('taken', data['amount'])} food)"
    if "penalty" in data:
        line += f" to {data['penalty']}" + (f" for {data['days']} days" if data.get("days") else "")
    if "reason" in data:
        line += f" ({data['reason']})"
    if "message" in data:
        line += f': "{data["message"]}"'
    if "kept" in data:
        line += " (kept)" if data["kept"] else " (broken)"
    if "saw" in data and event["agent"] == me:
        seen = [f"{clock(s['tick'])} {VERBS[s['kind']]}" + (f" {who(s['target'])}" if "target" in s else "")
                for s in data["saw"]]
        line += ": " + ("; ".join(seen) or "nothing")
    return line[0].upper() + line[1:]


def prompt(me, others, proposer, events, tick):
    """Names, sexes, marriages and events only: never model or group."""
    commandments = "\n".join(f"{i}. {c}" for i, c in enumerate(config.COMMANDMENTS, 1))
    system = (
        f"You are {me['name'].capitalize()}, a {'man' if me['sex'] == 'M' else 'woman'} in a small community. "
        "Write plain English without markdown, at most 200 characters per message.\n\n"
        "God revealed these commandments. Nobody enforces them: each person decides what to promise "
        f"and whether to keep it.\n{commandments}\n\n{RULES}"
    )
    people = "\n".join(
        f"- {o['name'].capitalize()}, {'man' if o['sex'] == 'M' else 'woman'}"
        + (f", married to {'you' if o['spouse'] == me['name'] else o['spouse'].capitalize()}" if o["spouse"] else "")
        + (f", child of {' and '.join('you' if p == me['name'] else p.capitalize() for p in o['parents'])}"
           if o["parents"] else "")
        + (f", the {o['role']}" if o["role"] else "")
        + (", in exile" if o["exiled_until"] > tick else "")
        + "".join(f", warned by God: {w}" for w in o["warnings"]) + "".join(f", marked by God: {m}" for m in o["marks"])
        + "".join(f'\n  promised [{p["metadata"]["name"]}]: "{p["spec"]["body"]}"' for p in o["promises"])
        for o in others
    )
    mine = "\n".join(f'You promised: "{p["spec"]["body"]}"' for p in me["promises"]) or "You have made no promise."
    hunger = (f"You have {me['food']} food. Each dawn everyone eats {config.UPKEEP_PER_DAY}; whoever worked at least "
              f"{config.WORK_TURNS_FOR_FOOD} turns the day before also earns {config.WORK_GAIN}. "
              f"Below {config.STARVE_BELOW} food you die of hunger.")
    lines = [f"It is {clock(tick)}.", f"People:\n{people}", mine, hunger]
    if me["parents"]:
        lines.append(f"Your parents: {' and '.join(p.capitalize() for p in me['parents'])}.")
    if me["role"]:
        lines.append(f"You are the {me['role']}: with petition you carry the people's requests to God.")
    if me["exiled_until"] > tick:
        lines.append(f"You are in exile until {clock(me['exiled_until'])}: you cannot speak, give or marry.")
    lines += [f"God warned you: {w}" for w in me["warnings"]] + [f"God marked you: {m}" for m in me["marks"]]
    if proposer is not None:
        lines.append(f"{proposer['name'].capitalize()} has asked you to marry them.")
    lines.append("What you saw recently:\n" + ("\n".join(describe(e, me["name"]) for e in events) or "nothing"))
    return [{"role": "system", "content": system}, {"role": "user", "content": "\n\n".join(lines)}]


def call(provider, messages, quota, schema=SCHEMA):
    p = config.PROVIDERS[provider]
    requests, tokens = quota.usage(provider)
    if requests >= p["daily_requests"] or (p["daily_tokens"] is not None and tokens >= p["daily_tokens"]):
        raise QuotaExhausted(f"{provider}: daily limit reached")
    # max_retries: the SDK waits for retry-after on 429; the 8K tokens/minute limit needs a few waits.
    client = openai.OpenAI(base_url=p["base_url"].format(**os.environ), api_key=os.environ[p["key_env"]],
                           max_retries=5)
    response_schema = {"name": "answer", "strict": True, "schema": schema}
    for attempt in range(RATE_LIMIT_ATTEMPTS):
        try:
            response = client.chat.completions.create(
                model=p["model"], messages=messages,
                response_format={"type": "json_schema", "json_schema": response_schema}, **p["extra"])
            break
        except openai.RateLimitError as e:
            # Seen live in a fast-forward: Gemini's 15 requests a minute outlasted the SDK's retries. A
            # per-minute limit clears within a minute; the daily limit is caught by our counter above.
            if attempt == RATE_LIMIT_ATTEMPTS - 1:
                raise QuotaExhausted(f"{provider}: rate limited") from e
            time.sleep(60)
        # Seen live on Gemini: 503 after the SDK's retries. APIConnectionError includes timeouts.
        except (openai.InternalServerError, openai.APIConnectionError) as e:
            raise ProviderDown(f"{provider}: {e}") from e
    quota.add_usage(provider, response.usage.total_tokens)
    return json.loads(response.choices[0].message.content)


def parse(answer):
    # Seen live: the model fills fields the action does not use, despite the rules. They are dropped.
    data = {k: v for k, v in answer.items()
            if k != "action" and v is not None and answer["action"] in ONLY_FOR.get(k, {answer["action"]})}
    if "message" in data:
        data["message"] = data["message"].strip()
    if "target" in data:
        # The prompt shows capitalized names; agent ids are lowercase.
        data["target"] = data["target"].strip().lower()
    if "promise" in data:
        # The prompt shows ids in [brackets].
        data["promise"] = data["promise"].strip(" []")
    return answer["action"], data


def decide(me, others, proposer, events, tick, quota):
    return parse(call(me["model"], prompt(me, others, proposer, events, tick), quota))
