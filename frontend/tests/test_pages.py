from __future__ import annotations

import pathlib

import pytest
from streamlit.testing.v1 import AppTest

PAGES = pathlib.Path(__file__).resolve().parents[1] / "pages"


def load(name: str) -> AppTest:
    at = AppTest.from_file(str(PAGES / name), default_timeout=90)
    at.run()
    return at


def click(at: AppTest, label: str) -> AppTest:
    btn = next(b for b in at.button if b.label == label)
    return btn.click().run()


def assert_clean(at: AppTest) -> None:
    assert not at.exception, [e.value for e in at.exception]
    assert not at.error, [e.value for e in at.error]


def has_rule(at: AppTest, name: str) -> bool:
    return any(name in set(df.value["name"]) for df in at.dataframe if "name" in df.value.columns)


def text(at: AppTest) -> str:
    return " ".join(m.value for m in at.markdown)


@pytest.mark.parametrize("name", ["overview.py", "datasets.py", "quality.py", "drift.py", "schema.py",
                                  "root_cause.py", "settings.py"])
def test_every_page_renders_with_demo_data(demo_api, name):
    assert_clean(load(name))


def test_overview_assessment(demo_api):
    at = click(load("overview.py"), "Run assessment")
    assert_clean(at)
    t = text(at)
    assert "Quality score" in t and "Drifted features" in t and "What to investigate first" in t


def test_quality_validation_and_score(demo_api):
    at = click(load("quality.py"), "Run validation")
    assert_clean(at)
    assert "Failed" in text(at) and len(at.dataframe) >= 1
    at = click(at, "Calculate score")
    assert_clean(at)
    assert "Overall score" in text(at) and "Dimension breakdown" in text(at)


def test_quality_compare_and_history(demo_api):
    at = click(load("quality.py"), "Compare two versions")
    assert_clean(at)
    assert "Change" in text(at)
    at = click(at, "Score every version")
    assert_clean(at)


def test_drift_detection_flags_injected_shift(demo_api):
    at = click(load("drift.py"), "Detect drift")
    assert_clean(at)
    t = text(at)
    assert "Drifted features" in t and "Feature detail" in " ".join(s.value for s in at.subheader)


def test_schema_page_shows_breaking_changes(demo_api):
    at = load("schema.py")
    assert_clean(at)
    t = text(at)
    assert "Breaking changes" in t and "discount" in " ".join(str(df.value) for df in at.dataframe)


def test_root_cause_report(demo_api):
    at = click(load("root_cause.py"), "Analyse root causes")
    assert_clean(at)
    t = text(at)
    assert "Findings" in t and "possible" in t.lower()
    assert len(at.expander) >= 3


def test_settings_creates_edits_and_deletes_rule(demo_api):
    at = load("settings.py")
    at.text_input(key="new_name").set_value("UI test rule")
    at.selectbox(key="new_type").select("duplicate_rows")
    at = click(at, "Create rule")
    assert_clean(at)
    at = load("settings.py")
    assert has_rule(at, "UI test rule")
    # invalid JSON is reported, not crashed
    at.selectbox(key="new_type").select("missing_values")
    at.text_area(key="new_params_missing_values").set_value("{not json")
    at = click(at, "Create rule")
    assert not at.exception and any("valid JSON" in e.value for e in at.error)


def test_rule_can_be_edited_and_deleted_from_the_ui(demo_api):
    at = load("settings.py")
    at.text_input(key="new_name").set_value("Temp rule")
    at.selectbox(key="new_type").select("constant_column")
    at = click(at, "Create rule")
    at = load("settings.py")
    assert has_rule(at, "Temp rule")
    at.selectbox(key="rule_pick").select("Temp rule (constant_column)")
    at.run()
    rid = next(s.key for s in at.selectbox if s.key and s.key.startswith("sev_"))
    at.selectbox(key=rid).select("critical")
    at = click(at, "Save changes")
    assert_clean(at)
    at = load("settings.py")
    at.selectbox(key="rule_pick").select("Temp rule (constant_column)")
    at.run()
    at = click(at, "Delete rule")
    assert_clean(at)
    assert not has_rule(load("settings.py"), "Temp rule")


def test_baseline_replacement_requires_confirmation(demo_api):
    at = load("settings.py")
    at.selectbox[0].select(2)  # version to approve
    at.text_input[0].set_value("promote v2 in test")
    at = click(at, "Set baseline")
    assert any("BASELINE_CONFIRMATION_REQUIRED" in e.value for e in at.error)


def test_datasets_explorer_tabs(demo_api):
    at = load("datasets.py")
    assert_clean(at)
    assert len(at.tabs) >= 7
    assert len(at.dataframe) >= 3


def test_empty_backend_is_handled_gracefully(empty_api):
    for name in ("overview.py", "datasets.py"):
        at = load(name)
        assert not at.exception, [e.value for e in at.exception]
    at = load("quality.py")
    assert not at.exception and any("No datasets" in i.value for i in at.markdown)


def test_unreachable_backend_shows_helpful_error(monkeypatch):
    monkeypatch.setenv("DS_API_URL", "http://127.0.0.1:9/api")
    at = load("overview.py")
    assert not at.exception
    assert any("Cannot reach" in e.value for e in at.error)


def test_app_shell_sidebar_and_navigation(demo_api):
    at = AppTest.from_file(str(PAGES.parent / "app.py"), default_timeout=90)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    picker = at.sidebar.selectbox[0]
    assert picker.label == "Active dataset" and "Retail Transactions" in picker.options
    assert picker.value == "Retail Transactions"
    at.sidebar.selectbox[0].select("Customer Master").run()
    assert not at.exception
    assert at.session_state["dataset_id"] is not None
    assert any("synthetic" in m.value for m in at.sidebar.markdown)


def test_first_time_user_can_build_the_whole_demo_from_the_ui(empty_api):
    import requests

    at = load("datasets.py")
    at = click(at, "Load Retail Transactions v1 + Customer Master")
    assert_clean(at)
    names = {d["name"] for d in requests.get(empty_api + "/datasets").json()["items"]}
    assert names == {"Retail Transactions", "Customer Master"}

    at = click(at, "Configure demo rules + baseline")
    assert_clean(at)
    retail = next(d for d in requests.get(empty_api + "/datasets").json()["items"] if d["name"] == "Retail Transactions")
    assert len(requests.get(empty_api + "/rules", params={"dataset_id": retail["id"]}).json()) == 36
    assert [b["version_number"] for b in requests.get(f"{empty_api}/datasets/{retail['id']}/baselines").json()
            if b["is_active"]] == [1]

    at = click(at, "Add Retail Transactions v2")
    assert_clean(at)
    assert requests.get(f"{empty_api}/datasets/{retail['id']}").json()["version_count"] == 2

    # The analysis pages now work on top of what the UI built.
    at = click(load("root_cause.py"), "Analyse root causes")
    assert_clean(at)
    assert "Findings" in text(at)
    at = click(load("drift.py"), "Detect drift")
    assert_clean(at)
