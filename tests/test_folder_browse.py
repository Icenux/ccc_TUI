import os

from ccc import folder_browse as fb


def _tree(tmp_path):
    for name in ("work/alpha/.git", "work/beta", "work/.hidden", "work/alpha/src", "disk/proj-x/.git",
                 "disk/proj-x/inner/.git", "work/node_modules/dep/.git"):
        (tmp_path / name).mkdir(parents=True)
    (tmp_path / "work" / "file.txt").write_text("x")
    return tmp_path


def test_visible_folders_skip_hidden_and_files(tmp_path):
    _tree(tmp_path)
    assert fb.visible_folders(tmp_path / "work") == ["alpha", "beta", "node_modules"]
    assert ".hidden" in fb.visible_folders(tmp_path / "work", show_hidden=True)
    assert fb.visible_folders(tmp_path / "missing") == []


def test_root_listing_skips_pseudo_filesystems():
    names = fb.visible_folders("/")
    assert "proc" not in names and "sys" not in names and "dev" not in names


def test_completions_by_prefix_and_hidden_only_when_typed(tmp_path):
    _tree(tmp_path)
    base = str(tmp_path / "work") + "/"
    assert fb.completions(base + "a") == [base + "alpha/"]
    assert fb.completions(base) == [base + "alpha/", base + "beta/", base + "node_modules/"]
    assert fb.completions(base + ".") == [base + ".hidden/"]
    assert fb.completions("/no/such/folder/x") == []


def test_tab_completes_to_what_choices_share(tmp_path):
    for name in ("project-a", "project-b", "other"):
        (tmp_path / name).mkdir()
    base = str(tmp_path) + "/"
    assert fb.complete(base + "pro") == (base + "project-", [base + "project-a/", base + "project-b/"])
    assert fb.complete(base + "oth")[0] == base + "other/"
    assert fb.complete(base + "zzz") == (base + "zzz", [])


def test_tilde_stays_tilde(tmp_path, monkeypatch):
    (tmp_path / "DEV" / "ccc").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert fb.completions("~/DEV/c") == ["~/DEV/ccc/"]
    assert fb.short(tmp_path / "DEV", home=str(tmp_path)) == "~/DEV"
    assert fb.breadcrumb(tmp_path / "DEV") == "~ / DEV /"
    assert fb.breadcrumb("/mnt/samsung") == "/ mnt / samsung /"
    assert fb.breadcrumb("/") == "/"


def test_nearest_existing_folder(tmp_path):
    _tree(tmp_path)
    assert fb.nearest_existing(str(tmp_path / "work" / "alpha" / "nope" / "deeper"), "/") == str(tmp_path / "work" / "alpha")
    assert fb.nearest_existing("", str(tmp_path)) == str(tmp_path)


def test_find_projects_matches_name_skips_hidden_and_nested(tmp_path):
    _tree(tmp_path)
    found = list(fb.find_projects("", [str(tmp_path)]))
    assert str(tmp_path / "work" / "alpha") in found and str(tmp_path / "disk" / "proj-x") in found
    assert not any("inner" in p or "node_modules" in p or ".hidden" in p for p in found)
    assert list(fb.find_projects("PROJ", [str(tmp_path)])) == [str(tmp_path / "disk" / "proj-x")]


def test_find_projects_stops_at_deadline_and_on_request(tmp_path):
    _tree(tmp_path)
    ticks = iter(range(0, 1000, 100))
    assert list(fb.find_projects("", [str(tmp_path)], seconds=50, clock=lambda: next(ticks))) == []
    assert list(fb.find_projects("", [str(tmp_path)], stop=lambda: True)) == []


def test_find_projects_depth_limit(tmp_path):
    (tmp_path / "a" / "b" / "c" / "deep" / ".git").mkdir(parents=True)
    assert list(fb.find_projects("deep", [str(tmp_path)], max_depth=2)) == []
    assert list(fb.find_projects("deep", [str(tmp_path)], max_depth=5)) == [str(tmp_path / "a" / "b" / "c" / "deep")]


def test_mount_points_only_other_disks(tmp_path):
    mounts = tmp_path / "mounts"
    mounts.write_text("/dev/sda1 / ext4 rw 0 0\n/dev/sdb1 /mnt/samsung ext4 rw 0 0\ntmpfs /run/user/1000 tmpfs rw 0 0\n"
                      "/dev/sdc1 /media/xia/USB\\040STICK vfat rw 0 0\n")
    assert fb.mount_points(str(mounts), user="xia") == ["/mnt/samsung", "/media/xia/USB STICK"]
