from api.semantics.loader import (
    MAX_RENDER_BYTES,
    SEMANTICS_DIR,
    load_semantics,
    render_semantics,
)
from tests.eval.runner import load_cases


def test_semantics_load_from_the_package_directory():
    data = load_semantics()
    assert data["glossary"], "glossary.yaml must not be empty"
    assert data["exemplars"], "exemplars.yaml must not be empty"


def test_every_glossary_entry_has_a_term_and_a_definition():
    for entry in load_semantics()["glossary"]:
        assert entry.get("term"), f"missing term in {entry}"
        assert entry.get("definition"), f"missing definition in {entry['term']}"


def test_every_exemplar_has_a_question_and_a_select():
    for entry in load_semantics()["exemplars"]:
        assert entry.get("question"), f"missing question in {entry}"
        sql = entry.get("sql", "")
        assert sql.strip().upper().startswith(("SELECT", "WITH")), entry["question"]


def test_glossary_terms_are_unique():
    terms = [e["term"].lower() for e in load_semantics()["glossary"]]
    assert len(terms) == len(set(terms))


def test_render_includes_terms_and_questions():
    data = load_semantics()
    rendered = render_semantics(data)
    assert data["glossary"][0]["term"] in rendered
    assert data["exemplars"][0]["question"] in rendered


def test_render_stays_within_the_prompt_budget():
    rendered = render_semantics(load_semantics())
    assert len(rendered.encode("utf-8")) <= MAX_RENDER_BYTES, (
        "The semantics block has outgrown the prompt budget. Convert the "
        "glossary into a lookup_term tool instead of enlarging the prompt."
    )


def test_a_malformed_file_raises(tmp_path):
    (tmp_path / "glossary.yaml").write_text("- definition: no term here\n", encoding="utf-8")
    (tmp_path / "exemplars.yaml").write_text("[]\n", encoding="utf-8")
    try:
        load_semantics(tmp_path)
    except ValueError:
        return
    raise AssertionError("expected ValueError for a glossary entry with no term")


def test_semantics_dir_is_inside_the_api_package():
    assert SEMANTICS_DIR.name == "semantics"


def test_every_glossary_term_is_covered_by_an_eval_case():
    """A prompt change is a behaviour change, so every term needs a case."""
    covered = {
        str(term).lower()
        for case in load_cases()
        for term in (case.get("covers_terms") or [])
    }
    terms = {entry["term"].lower() for entry in load_semantics()["glossary"]}
    missing = terms - covered
    assert not missing, f"glossary terms with no eval case: {sorted(missing)}"
