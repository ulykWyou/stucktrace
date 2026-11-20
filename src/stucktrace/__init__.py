"""stucktrace — learning-trajectory telemetry & reflection for Moodle + OPT_Mentor + Hermes.

Public API::

    import stucktrace as st
    st.start_session(student_id="s123", task="loops")   # auto-hooks Jupyter cells
    st.mark_confusion("I don't get the loop")
    st.log_viz(step=3, line=5)
    st.generate_report("student")          # or "instructor"

In Jupyter you can also use ``%load_ext stucktrace``.
"""
from ._version import __version__
from .adapters import HermesClient, LocalReportGenerator, MoodleAdapter, OPTMentorAdapter
from .compressor import CompressedTrajectory, CompressorConfig, compress, error_signature
from .reporter import Report, generate_report, spoiler_guard
from .tracker import (
    Session,
    end_session,
    get_session,
    log_viz,
    mark_confusion,
    set_task,
    start_session,
    track,
)

init_session = start_session  # alias used in docs / notebooks

__all__ = [
    "__version__",
    "start_session", "init_session", "end_session", "get_session", "set_task",
    "mark_confusion", "log_viz", "track", "generate_report",
    "Session", "Report", "compress", "CompressorConfig", "CompressedTrajectory", "error_signature",
    "spoiler_guard", "MoodleAdapter", "OPTMentorAdapter", "HermesClient", "LocalReportGenerator",
    "load_ipython_extension", "unload_ipython_extension",
]


def load_ipython_extension(ipython):  # %load_ext stucktrace
    from .tracker import _current, install_jupyter_hooks
    if _current is None:
        start_session(jupyter=False)
    install_jupyter_hooks(ipython)
    print("stucktrace: tracking cells. Use %stucktrace task <name>, %confused <text>, %stucktrace report")


def unload_ipython_extension(ipython):
    from .tracker import uninstall_jupyter_hooks
    uninstall_jupyter_hooks(ipython)
