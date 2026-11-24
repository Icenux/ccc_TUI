from ccc.launch_options import DEFAULT_SELECTION, FORK, SESSION_NEW, SESSION_RESUME, toggle
from ccc.presets import LaunchChoice, list_presets, load_last, save_last, save_preset
from ccc.storage import Storage


def test_last_choice_round_trip_resets_session(tmp_path):
    storage = Storage(tmp_path / "h.db")
    assert load_last(storage) is None
    sel = toggle(toggle(toggle(DEFAULT_SELECTION, "model_opus", True), SESSION_RESUME, True), FORK, True)
    save_last(storage, LaunchChoice(sel, auto_continue=False, remote_control=False))
    last = load_last(storage)
    assert "model_opus" in last.selection
    assert SESSION_NEW in last.selection and SESSION_RESUME not in last.selection and FORK not in last.selection
    assert last.auto_continue is False and last.remote_control is False


def test_unknown_keys_are_dropped_and_required_groups_kept(tmp_path):
    storage = Storage(tmp_path / "h.db")
    storage.set_setting("last_launch_selection", '{"selection": ["model_gone", "effort_max"], "auto_continue": true, "remote_control": true}')
    last = load_last(storage)
    assert "model_gone" not in last.selection and "effort_max" in last.selection
    assert "perm_skip" in last.selection and SESSION_NEW in last.selection  # required groups filled


def test_presets_save_and_overwrite(tmp_path):
    storage = Storage(tmp_path / "h.db")
    assert list_presets(storage) == {}
    save_preset(storage, "fast", LaunchChoice(toggle(DEFAULT_SELECTION, "model_haiku", True), True, False))
    save_preset(storage, "deep", LaunchChoice(toggle(DEFAULT_SELECTION, "effort_max", True), True, True))
    save_preset(storage, "fast", LaunchChoice(toggle(DEFAULT_SELECTION, "model_sonnet", True), False, False))
    presets = list_presets(storage)
    assert sorted(presets) == ["deep", "fast"]
    assert "model_sonnet" in presets["fast"].selection and presets["fast"].auto_continue is False


def test_corrupt_settings_are_ignored(tmp_path):
    storage = Storage(tmp_path / "h.db")
    storage.set_setting("last_launch_selection", "not json")
    storage.set_setting("launch_presets", "[1, 2]")
    assert load_last(storage) is None and list_presets(storage) == {}


def test_mcp_servers_are_remembered_with_the_choice(tmp_path):
    storage = Storage(tmp_path / "h.db")
    sel = toggle(DEFAULT_SELECTION, "mcp:context7", True)
    save_last(storage, LaunchChoice(sel))
    assert {"mcp_select", "mcp:context7"} <= load_last(storage).selection


def test_default_preset_is_what_the_dialog_opens_with(tmp_path):
    from ccc import presets
    from ccc.storage import Storage
    storage = Storage(tmp_path / "h.db")
    plain, name = presets.opening_choice(storage)
    assert name is None and plain.selection == presets.DEFAULT_SELECTION
    presets.save_last(storage, presets.LaunchChoice(frozenset({"session_new", "perm_plan"})))
    assert presets.opening_choice(storage)[0].selection >= {"perm_plan"}  # nothing set: what was used last
    presets.save_preset(storage, "deep", presets.LaunchChoice(frozenset({"session_new", "perm_skip", "model_opus"}), False, True))
    presets.set_default_preset(storage, "deep")
    choice, name = presets.opening_choice(storage)
    assert name == "deep" and "model_opus" in choice.selection and not choice.auto_continue
    presets.delete_preset(storage, "deep")  # a deleted one is no default
    assert presets.default_preset_name(storage) is None
    assert presets.opening_choice(storage)[1] is None
    presets.set_default_preset(storage, "gone")  # a name that isn't a preset
    assert presets.default_preset_name(storage) is None
