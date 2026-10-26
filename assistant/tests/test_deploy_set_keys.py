import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "set_keys", Path(__file__).resolve().parent.parent / "deploy" / "set_keys.py")
set_keys = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(set_keys)


def test_update_env_replaces_all_duplicates_and_keeps_the_rest():
    text = ("# комментарий\nTELEGRAM_BOT_TOKEN=1:old\nCLAUDE_MODEL=claude-haiku-5-5\n"
            "ANTHROPIC_API_KEY = sk-ant-old\nTELEGRAM_BOT_TOKEN=1:older\n")
    result = set_keys.update_env_text(text, {"TELEGRAM_BOT_TOKEN": "1:new", "ANTHROPIC_API_KEY": "sk-ant-new"})
    assert result == ("# комментарий\nTELEGRAM_BOT_TOKEN=1:new\nCLAUDE_MODEL=claude-haiku-5-5\n"
                      "ANTHROPIC_API_KEY=sk-ant-new\nTELEGRAM_BOT_TOKEN=1:new\n")
    assert set_keys.env_value(result, "TELEGRAM_BOT_TOKEN") == "1:new"


def test_update_env_appends_missing_key():
    result = set_keys.update_env_text("OWNER_ID=42", {"STT_API_KEY": "gsk_new"})
    assert result == "OWNER_ID=42\nSTT_API_KEY=gsk_new\n"


def test_env_value_last_wins_and_default():
    text = "STT_BASE_URL=https://a/v1\n#STT_BASE_URL=https://x\nSTT_BASE_URL=\"https://b/v1\"\n"
    assert set_keys.env_value(text, "STT_BASE_URL") == "https://b/v1"
    assert set_keys.env_value(text, "MISSING", "dflt") == "dflt"


def test_mask_hides_every_secret():
    assert set_keys.mask("bot 1:abc failed, key sk-x", ["1:abc", "sk-x", ""]) == "bot *** failed, key ***"
