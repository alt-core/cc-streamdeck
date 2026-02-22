"""Tests for IPC protocol encode/decode."""

from cc_streamdeck.protocol import (
    PermissionChoice,
    PermissionRequest,
    PermissionResponse,
    decode_request,
    decode_response,
    encode,
)


class TestPermissionRequest:
    def test_round_trip(self, sample_request):
        data = encode(sample_request)
        decoded = decode_request(data)
        assert decoded.tool_name == "Bash"
        assert decoded.tool_input == {"command": "rm -rf node_modules"}
        assert len(decoded.choices) == 3
        assert decoded.choices[0].label == "Allow"
        assert decoded.choices[1].behavior == "deny"
        assert decoded.choices[2].updated_permissions == [
            {"type": "toolAlwaysAllow", "tool": "Bash"}
        ]

    def test_empty_request(self):
        req = PermissionRequest()
        data = encode(req)
        decoded = decode_request(data)
        assert decoded.tool_name == ""
        assert decoded.choices == []

    def test_ndjson_format(self, sample_request):
        data = encode(sample_request)
        assert data.endswith(b"\n")
        assert data.count(b"\n") == 1


class TestPermissionResponse:
    def test_round_trip_ok(self):
        choice = PermissionChoice(label="Allow", behavior="allow")
        resp = PermissionResponse(status="ok", chosen=choice)
        data = encode(resp)
        decoded = decode_response(data)
        assert decoded.status == "ok"
        assert decoded.chosen is not None
        assert decoded.chosen.label == "Allow"
        assert decoded.chosen.behavior == "allow"

    def test_round_trip_no_device(self):
        resp = PermissionResponse(status="no_device")
        data = encode(resp)
        decoded = decode_response(data)
        assert decoded.status == "no_device"
        assert decoded.chosen is None

    def test_round_trip_error(self):
        resp = PermissionResponse(status="error", error_message="Timeout")
        data = encode(resp)
        decoded = decode_response(data)
        assert decoded.status == "error"
        assert decoded.error_message == "Timeout"

    def test_deny_with_message(self):
        choice = PermissionChoice(label="Deny", behavior="deny", message="Not allowed")
        resp = PermissionResponse(status="ok", chosen=choice)
        data = encode(resp)
        decoded = decode_response(data)
        assert decoded.chosen.behavior == "deny"
        assert decoded.chosen.message == "Not allowed"
