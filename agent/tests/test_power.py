import asyncio

from tvwake import power


class Fake:
    def __init__(self, answers):
        self.answers = list(answers)  # ping results, one per check

    async def ping(self, ip):
        return self.answers.pop(0) if self.answers else False

    async def tcp_open(self, ip, port):
        return False

    async def send_wol(self):
        pass


def run_report(make_cfg, monkeypatch, answers, wait_s=180):
    monkeypatch.setattr(power, "OFF_POLL_S", 0)
    monkeypatch.setattr(power, "OFF_WAIT_S", wait_s)
    toasts = []

    async def toast(text):
        toasts.append(text)

    asyncio.run(power.report_when_off(make_cfg(PC_NAME="MoralMe"), Fake(answers), toast))
    return toasts


def test_toast_when_the_pc_goes_quiet(make_cfg, monkeypatch):
    toasts = run_report(make_cfg, monkeypatch, [True, True, False])
    assert len(toasts) == 1 and toasts[0].startswith("MoralMe is off")


def test_toast_when_it_stays_on(make_cfg, monkeypatch):
    toasts = run_report(make_cfg, monkeypatch, [True] * 10**6, wait_s=0.05)
    assert len(toasts) == 1 and "still on" in toasts[0]
