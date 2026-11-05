class FakeK8s:
    """Stands in for the k8s API: keeps the last status written for each agent."""

    def __init__(self):
        self.status = {}

    def patch_namespaced_custom_object_status(self, name, body, **_):
        self.status.setdefault(name, {}).update(body["status"])
