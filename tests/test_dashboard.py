from pathlib import Path

from streamlit.testing.v1 import AppTest

from config.settings import Settings


def app(tmp_path, monkeypatch):
    # Isolate the dashboard from the user's collection database.
    monkeypatch.setenv("DATABASE_PATH",str(tmp_path/"ui.db"))
    monkeypatch.setenv("DATA_SOURCE","demo")
    return AppTest.from_file(str(Path(__file__).resolve().parents[1]/"src/app/main.py"),default_timeout=30).run()


def test_three_screens_and_simulation(tmp_path,monkeypatch):
    at = app(tmp_path,monkeypatch)
    assert not at.exception
    assert len(at.metric) >= 4
    at.sidebar.radio[1].set_value("Mô phỏng thực hiện lệnh").run()
    assert not at.exception
    assert at.metric[0].value == "100.0%"
    assert at.metric[5].value == "125,000"
    at.number_input[0].set_value(4000).run()
    assert not at.exception
    assert at.metric[2].value == "500"
    at.sidebar.radio[1].set_value("Stress danh mục").run()
    assert not at.exception
    assert any(m.label == "Coverage phần đủ dữ liệu" for m in at.metric)


def test_disconnected_no_data_and_replay(tmp_path,monkeypatch):
    at = app(tmp_path,monkeypatch)
    at.sidebar.selectbox[0].set_value("neo").run()
    assert not at.exception
    assert any("DISCONNECTED" in w.value for w in at.warning)
    at.sidebar.selectbox[0].set_value("vnstock").run()
    assert not at.exception
    assert any("NO DATA" in i.value for i in at.info)
    at.sidebar.selectbox[0].set_value("demo").run()
    at.sidebar.radio[0].set_value("Replay lịch sử").run()
    assert not at.exception
    assert any("NO DATA" in i.value for i in at.info)


def test_store_and_replay(tmp_path,monkeypatch):
    at = app(tmp_path,monkeypatch)
    at.sidebar.button[0].click().run()
    assert not at.exception
    at.sidebar.radio[0].set_value("Replay lịch sử").run()
    assert not at.exception
    assert any("REPLAY" in i.value for i in at.info)
