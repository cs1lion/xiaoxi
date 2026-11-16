import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.dialogue.ambient import (
    build_mumble,
    choose_content,
    hint_from_lesson,
    hint_from_pending_work,
)


def main() -> None:
    text, refs = choose_content(
        discovery={"text": "发现一条新线索", "source_refs": ["mem:1"]},
        lesson={"id": 7, "action": "先列提纲再动笔"},
    )
    assert text == "发现一条新线索"
    assert refs == ["mem:1"]

    text, refs = choose_content(lesson={"id": 42, "action": "写完先通读一遍"})
    assert text == "写完先通读一遍"
    assert refs == ["lesson:42"]

    text, refs = choose_content(pending_work={"id": "draft-1", "title": "昨晚的草稿还差结尾"})
    assert text == "昨晚的草稿还差结尾"
    assert refs == ["work:draft-1"]

    mumble = build_mumble(text=text, reason="ambient_work_hint", source_refs=refs, now=1000.0)
    assert mumble["type"] == "mumble"
    assert mumble["source_refs"] == ["work:draft-1"]

    assert hint_from_lesson(None) == (None, [])
    assert hint_from_pending_work({}) == (None, [])
    print("test_ambient_memory_hints passed")


if __name__ == "__main__":
    main()
