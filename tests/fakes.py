class FakeK8s:
    """Stands in for the k8s API: keeps the last status written for each agent and what was created."""

    def __init__(self):
        self.status = {}
        self.created = []

    def patch_namespaced_custom_object_status(self, name, body, **_):
        self.status.setdefault(name, {}).update(body["status"])

    def create_namespaced_custom_object(self, body, **_):
        self.created.append(body)
        return body
