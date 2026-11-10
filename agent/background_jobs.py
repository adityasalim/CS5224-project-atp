"""Run registered AgentCore tasks independently of HTTP response generators."""
import threading
import traceback


class SessionBusy(Exception):
    def __init__(self, request_id):
        self.request_id = request_id
        super().__init__("Another request is still running in this conversation")


class BackgroundJobs:
    def __init__(self, app, thread_factory=threading.Thread):
        self.app = app
        self.thread_factory = thread_factory
        self.lock = threading.Lock()
        self.active = {}

    def busy(self, key):
        with self.lock:
            return self.active.get(key)

    def submit(self, key, request_id, claim, work):
        if not callable(getattr(self.app, "add_async_task", None)) or not callable(getattr(self.app, "complete_async_task", None)):
            raise RuntimeError("Upgrade the bedrock-agentcore SDK before enabling background planning")
        with self.lock:
            active = self.active.get(key)
            if active and active != request_id:
                raise SessionBusy(active)
            if not claim():
                return False
            task_id = self.app.add_async_task("trip_planning", {"requestId": request_id})
            self.active[key] = request_id

            def worker():
                try:
                    work()
                except BaseException:
                    traceback.print_exc()
                finally:
                    # End registration even if planning or checkpointing failed.
                    try:
                        self.app.complete_async_task(task_id)
                    finally:
                        with self.lock:
                            if self.active.get(key) == request_id:
                                self.active.pop(key, None)
            try:
                self.thread_factory(target=worker, daemon=True).start()
            except BaseException:
                self.active.pop(key, None)
                self.app.complete_async_task(task_id)
                raise
            return True
