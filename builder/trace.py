"""The plain-text trace the rules ask for (README, "Rules"): start and end time, each model call
(model and a one-line purpose), what was created on Freya, and every test run on our own agent."""
import datetime
import pathlib


def _now():
    return datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")


def one_line(text, limit=160):
    return " ".join(str(text).split())[:limit]


class Trace:
    def __init__(self, path):
        self.path = pathlib.Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.model_calls = 0
        self._f = self.path.open("w", encoding="utf-8")

    def write(self, kind, text):
        self._f.write(f"{_now()}  {kind:<8} {text}\n")
        self._f.flush()

    def start(self, **info):
        self.write("START", " ".join(f"{k}={v}" for k, v in info.items()))

    def model_call(self, model, purpose):
        self.model_calls += 1
        self.write("MODEL", f"#{self.model_calls} {model}: {one_line(purpose)}")

    def freya(self, text):
        self.write("FREYA", one_line(text, 400))

    def test(self, text):
        self.write("TEST", one_line(text, 400))

    def note(self, text):
        self.write("NOTE", one_line(text, 400))

    def end(self, **info):
        self.write("END", " ".join(f"{k}={v}" for k, v in info.items()) + f" model_calls={self.model_calls}")
        self._f.close()
