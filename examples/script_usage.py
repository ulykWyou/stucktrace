"""Using stucktrace in a plain Python script (no Jupyter)."""
import stucktrace as st

st.start_session(student_id="s42", course="CS1302", jupyter=False)


@st.track("average")              # every call = one recorded attempt
def average(xs):
    return sum(xs) / len(xs)


for data in ([1, 2, 3], [], [4, 5]):
    try:
        print(average(data))
    except ZeroDivisionError:
        st.mark_confusion("why does an empty list crash?")

# Context-manager form. Keep try/except OUTSIDE the `with`, so the failure is recorded
# (stucktrace never swallows the exception — it records it and re-raises).
try:
    with st.track("parse", code="int('4.5')"):
        int("4.5")
except ValueError:
    pass

st.get_session().save("script_session.jsonl")
print(st.generate_report("instructor", backend="offline"))
