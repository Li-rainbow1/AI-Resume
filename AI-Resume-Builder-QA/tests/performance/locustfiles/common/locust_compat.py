"""管理 Locust 采样结束及 CSV 写协程的退出顺序。"""

import gevent
from locust.stats import StatsCSVFileWriter


def finish_run(environment) -> None:
    # 从独立协程关闭 runner，避免用户任务在自身清理过程中终止自身。
    if not getattr(environment, "_qa_finishing", False):
        environment._qa_finishing = True
        gevent.spawn(environment.runner.quit)


if not getattr(StatsCSVFileWriter, "_qa_writer_lifecycle", False):
    _original_stats_writer = StatsCSVFileWriter.stats_writer
    _original_close_files = StatsCSVFileWriter.close_files

    def _safe_stats_writer(self) -> None:
        self._qa_writer_greenlet = gevent.getcurrent()
        _original_stats_writer(self)

    def _close_files(self) -> None:
        writer = getattr(self, "_qa_writer_greenlet", None)
        if writer is not None and writer is not gevent.getcurrent():
            writer.kill(block=True)
        _original_close_files(self)

    StatsCSVFileWriter.stats_writer = _safe_stats_writer
    StatsCSVFileWriter.close_files = _close_files
    StatsCSVFileWriter._qa_writer_lifecycle = True
