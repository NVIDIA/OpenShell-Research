import json

import pytest

from privacy_guard.config import PolicyConfig
from privacy_guard.request_body import JsonHandler
from privacy_guard.scanners import PassthroughScanner


@pytest.mark.parametrize(
    ("raw_body", "selected_text_block_path", "expected_value"),
    [
        (
            b'{"messages":[{"role":"user","content":"hello"}],"model":"model-a"}',
            "/messages/0/content",
            {
                "messages": [{"role": "user", "content": "hello [test suffix]"}],
                "model": "model-a",
            },
        ),
        (
            b'{"contents":[{"parts":[{"text":"hello"}]}],"model":"model-b"}',
            "/contents/0/parts/0/text",
            {
                "contents": [{"parts": [{"text": "hello [test suffix]"}]}],
                "model": "model-b",
            },
        ),
    ],
)
def test_one_selected_text_block_can_be_scanned_and_explicitly_replaced(
    raw_body: bytes, selected_text_block_path: str, expected_value: object
) -> None:
    json_handler = JsonHandler()
    request_body = json_handler.normalize(raw_body, PolicyConfig())
    selected_text_block = next(
        text_block
        for text_block in request_body.text_blocks
        if text_block.path == selected_text_block_path
    )

    findings = PassthroughScanner().scan(selected_text_block.text)

    assert findings == ()
    test_suffix = " [test suffix]"
    replacement_text = selected_text_block.text + test_suffix
    reconstructed_body = json_handler.reconstruct(
        request_body, {selected_text_block.path: replacement_text}
    )

    assert json.loads(reconstructed_body) == expected_value
