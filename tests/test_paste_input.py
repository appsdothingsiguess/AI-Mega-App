"""Unit tests for large-paste collapse helpers (no TTY required)."""



from app.paste_input import (

    BRACKETED_PASTE_END,

    BRACKETED_PASTE_START,

    PasteBufferRegistry,

    apply_paste_to_buffer,

    buffer_has_collapsed_placeholder,

    extract_bracketed_pastes,

    make_placeholder,

    normalize_paste_text,

    should_collapse_paste,

)




def resolve_message(buffer: str, registry: PasteBufferRegistry) -> str:

    return registry.resolve_message(buffer)




def test_should_collapse_by_line_count() -> None:

    one_line = "a"

    assert should_collapse_paste(one_line) is False

    two_lines = "a\nb"

    assert should_collapse_paste(two_lines) is True

    four_lines = "a\nb\nc\nd"

    assert should_collapse_paste(four_lines) is True




def test_should_collapse_by_char_count() -> None:

    assert should_collapse_paste("x" * 149) is False

    assert should_collapse_paste("x" * 150) is True




def test_make_placeholder_lines() -> None:

    body = "\n".join(f"line {i}" for i in range(10))

    assert make_placeholder(1, body) == "[Pasted text #1 +9 lines]"




def test_make_placeholder_chars() -> None:

    body = "x" * 600

    assert make_placeholder(2, body) == "[Pasted text #2 +600 chars]"




def test_register_and_resolve_on_submit() -> None:

    registry = PasteBufferRegistry()

    body = "\n".join(f"row {i}" for i in range(8))

    _paste_id, token = registry.register(body)

    buffer = f"prefix {token} suffix"

    assert resolve_message(buffer, registry) == f"prefix {body} suffix"




def test_apply_paste_collapses_large_block() -> None:

    registry = PasteBufferRegistry()

    body = "\n".join(f"line {i}" for i in range(7))

    text, cursor = apply_paste_to_buffer("", 0, body, registry)

    assert "[Pasted text #1 +6 lines]" in text

    assert cursor == len(text)

    assert resolve_message(text, registry) == body




def test_apply_paste_collapses_four_line_user_scenario() -> None:

    registry = PasteBufferRegistry()

    body = "helo\nline two\nline three\nline four"

    text, cursor = apply_paste_to_buffer("helo ", 5, body, registry)

    assert "[Pasted text #1 +3 lines]" in text

    assert cursor == len(text)

    assert resolve_message(text, registry) == f"helo {body}"




def test_apply_paste_small_block_not_collapsed() -> None:

    registry = PasteBufferRegistry()

    body = "short note"

    text, cursor = apply_paste_to_buffer("hi ", 3, body, registry)

    assert text == "hi short note"

    assert cursor == len(text)




def test_expand_toggle_on_second_paste() -> None:

    registry = PasteBufferRegistry()

    body = "\n".join(f"line {i}" for i in range(6))

    collapsed, cursor = apply_paste_to_buffer("", 0, body, registry)

    token = "[Pasted text #1 +5 lines]"

    assert token in collapsed

    expanded, cursor = apply_paste_to_buffer(collapsed, cursor, "", registry)

    assert expanded == body

    assert cursor == len(body)

    recollapsed, cursor = apply_paste_to_buffer(expanded, 2, "", registry)

    assert token in recollapsed

    assert buffer_has_collapsed_placeholder(recollapsed)




def test_extract_bracketed_paste_sequences() -> None:

    raw = f"before{BRACKETED_PASTE_START}line1\nline2{BRACKETED_PASTE_END}after"

    assert extract_bracketed_pastes(raw) == ["line1\nline2"]




def test_normalize_paste_text_crlf() -> None:

    assert normalize_paste_text("a\r\nb\r") == "a\nb\n"




def test_multiple_placeholders_resolve() -> None:

    registry = PasteBufferRegistry()

    a = "\n".join("a" for _ in range(6))

    b = "\n".join("b" for _ in range(6))

    _id_a, tok_a = registry.register(a)

    _id_b, tok_b = registry.register(b)

    buffer = f"{tok_a} middle {tok_b}"

    assert resolve_message(buffer, registry) == f"{a} middle {b}"

