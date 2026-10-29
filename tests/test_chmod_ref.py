"""chmod-ref: what an @chmod: reference expands to, and the suggestions it offers."""

import asyncio
import os
import random
import shutil
import stat
import subprocess
import sys
import types

import pytest

LINUX = sys.platform.startswith("linux")


def _row(out, label):
    for line in out.splitlines():
        if line.startswith(f"  {label} "):
            return line[2 + len(label):].strip()
    return None


def _notes(out):
    lines, inside = [], False
    for line in out.splitlines():
        if not line.startswith(" "):
            inside = line == "Notes"
            continue
        if inside:
            lines.append(line[4:])
    return lines


def test_a_plain_mode(cmodes):
    assert cmodes.expand("755").splitlines() == [
        "Mode 755 (rwxr-xr-x)",
        "  octal     755",
        "  ls -l     -rwxr-xr-x for a file, drwxr-xr-x for a directory",
        "  owner     read, write, execute",
        "  group     read, execute",
        "  others    read, execute",
        "  special   none",
    ]


@pytest.mark.parametrize("text", ["755", "0755", "0o755", "0O755", "rwxr-xr-x", "-rwxr-xr-x", "100755",
                                  "00755", "000755"])
def test_the_same_mode_in_every_spelling(cmodes, text):
    assert cmodes.read_mode(text).bits == 0o755


@pytest.mark.parametrize("text", ["00755", "000755", "4755", "7777"])
def test_leading_zeros_and_special_digits_name_no_file_type(cmodes, text):
    mode = cmodes.read_mode(text)
    assert mode.ftype is None and not mode.gitlink


def test_exact_notes_for_the_special_bits(cmodes):
    # S only where the matching execute is missing; T only where others lack execute.
    assert _notes(cmodes.expand("4744")) == [
        "setuid: the program runs with its owner's rights, not those of the user who starts it "
        "(root-owned setuid programs, like passwd, run as root)"]
    assert "setuid is set but the owner has no execute (ls shows S), so it does nothing" in _notes(
        cmodes.expand("4654"))
    assert _notes(cmodes.expand("drwxr-S---")) == [
        "group can list the names but not enter the directory or open anything in it",
        "setgid on a directory: new files and directories inside take its group (new directories also get setgid)",
    ]
    assert "ls shows T because others have no execute; the sticky bit still applies to a directory" in _notes(
        cmodes.expand("drwxr-x--T"))
    assert not any("ls shows T" in note for note in _notes(cmodes.expand("drwxr-xr-t")))
    assert _notes(cmodes.expand("000")) == ["no one but root can read, change or run it"]
    assert _notes(cmodes.expand("300")) == [
        "owner can execute but not read: a compiled program runs, a script does not "
        "(its interpreter has to read it)"]


def test_symbolic_agrees_with_the_standard_library_for_every_mode(cmodes):
    for bits in range(0o10000):
        assert "-" + cmodes.symbolic(bits) == stat.filemode(stat.S_IFREG | bits)
        assert cmodes.read_mode(stat.filemode(stat.S_IFDIR | bits)).bits == bits
        assert cmodes.read_mode(cmodes.symbolic(bits)).bits == bits
        assert cmodes.read_mode(f"{bits:o}").bits == bits


@pytest.mark.parametrize("kind", [stat.S_IFREG, stat.S_IFDIR, stat.S_IFLNK, stat.S_IFCHR, stat.S_IFBLK,
                                  stat.S_IFIFO, stat.S_IFSOCK])
def test_st_mode_file_types_agree_with_the_standard_library(cmodes, kind):
    value = kind | 0o2754
    mode = cmodes.read_mode(f"{value:o}")
    assert mode.bits == 0o2754
    assert mode.ftype == stat.filemode(value)[0]
    assert _row(cmodes.expand(f"{value:o}"), "ls -l") == stat.filemode(value)
    # The same file type read back from ls -l's first letter.
    assert cmodes.read_mode(stat.filemode(value)).ftype == mode.ftype


def test_special_bits_and_their_notes(cmodes):
    out = cmodes.expand("4755")
    assert out.splitlines()[0] == "Mode 4755 (rwsr-xr-x)"
    assert _row(out, "special") == "setuid"
    assert any(note.startswith("setuid: the program runs with its owner's rights") for note in _notes(out))
    capital = _notes(cmodes.expand("4644"))
    assert any("ls shows S" in note for note in capital)
    sticky = cmodes.expand("drwxrwxrwt")
    assert sticky.splitlines()[0] == "Mode 1777 (rwxrwxrwt), a directory"
    assert any(note.startswith("sticky on a directory") for note in _notes(sticky))
    assert not any("others can write" in note for note in _notes(sticky))
    assert any(note.startswith("everyone can read, change and enter it") for note in _notes(sticky))
    setgid_dir = _notes(cmodes.expand("drwxr-sr-x"))
    assert any(note.startswith("setgid on a directory: new files") for note in setgid_dir)
    assert _notes(cmodes.expand("-rwxr-sr-x")) == ["setgid: the program runs with the file's group"]
    assert _notes(cmodes.expand("-rw-r--r-T")) == [
        "sticky on a file is ignored by Linux and macOS",
        "ls shows T because others have no execute; the sticky bit still applies to a directory",
    ]
    assert _notes(cmodes.expand("drwsr-xr-x")) == ["setuid on a directory is ignored by Linux and macOS"]


def test_directory_rights_are_named_as_directory_rights(cmodes):
    out = cmodes.expand("drwx-w-r--")
    assert _row(out, "owner") == "list names, add, rename and delete entries, enter"
    assert _row(out, "group") == "add, rename and delete entries"
    assert _row(out, "others") == "list names"
    assert any(note.startswith("group: write without execute on a directory does nothing")
               for note in _notes(out))
    assert _notes(cmodes.expand("drwxr-----")) == [
        "group can list the names but not enter the directory or open anything in it"]
    assert _notes(cmodes.expand("drwxr-x---")) == []
    assert any(note.startswith("group can list the names but not enter")
               for note in _notes(cmodes.expand("drwxr--r-x")))


@pytest.mark.parametrize("text, expected", [
    ("000", "no one but root can read, change or run it"),
    ("777", "everyone can read, change and run it"),
    ("646", "others can write: anyone on the machine can change it"),
    ("600", "only the owner (and root) can use it; ssh wants this (600 or 400) for private keys"),
    ("711", "group can execute but not read: a compiled program runs, a script does not "
            "(its interpreter has to read it)"),
    ("444", "read-only for the owner too: editors warn before saving; whether it can be deleted "
            "depends on the directory, not on the file"),
    ("077", "the group and others have read, write and execute, which the owner lacks: only the first "
            "class that matches a user counts, so the owner cannot use those rights"),
    ("604", "others have read, which the group lacks: only the first class that matches a user counts, "
            "so the group cannot use those rights"),
    ("drwxrwxrwx", "everyone can read, change and enter it"),
    ("drwxrwx-wx", "others can write: anyone on the machine can create, rename and delete entries in it "
                   "(the sticky bit, as on /tmp, would stop them deleting others' files)"),
    ("lrwxrwxrwx", "a symbolic link's own mode is not checked on Linux (it is always rwxrwxrwx there); "
                   "access depends on its target"),
    ("100664", "git records only 100644 or 100755 for a file; other bits are not kept"),
    ("160000", "160000 is how git records a submodule (a commit of another repository), not a file mode"),
    ("-rw-r--r--+", "the + after the bits means an access control list (ACL) grants more than these bits "
                    "show; getfacl lists it"),
    ("-rw-r--r--.", "the . after the bits means the file has an SELinux security context; ls -Z shows it"),
    ("-rw-r--r--@", "the @ after the bits means the file has extended attributes (macOS); xattr -l lists them"),
])
def test_notes(cmodes, text, expected):
    assert expected in _notes(cmodes.expand(text))


@pytest.mark.parametrize("text", ["644", "755", "100644", "100755", "-rw-r--r--", "750", "0644"])
def test_ordinary_modes_have_no_notes(cmodes, text):
    assert _notes(cmodes.expand(text)) == []


def test_st_mode_row_and_git_submodule(cmodes):
    out = cmodes.expand("100644")
    assert out.splitlines()[0] == "Mode 644 (rw-r--r--), a regular file"
    assert _row(out, "st_mode") == "100644 (file type and permission bits, as stat and git write them)"
    assert _row(out, "ls -l") == "-rw-r--r--"
    sub = cmodes.expand("160000")
    assert sub.splitlines()[0] == "Mode 000 (---------), a git submodule"
    assert _row(sub, "owner") is None
    with pytest.raises(ValueError, match="git submodule entry"):
        cmodes.expand('"160000 u+x"')


@pytest.mark.parametrize("base, change, expected", [
    # Worked by hand from the GNU coreutils manual ("Symbolic Modes"), umask 0.
    (0o644, "u+x", 0o744),
    (0o644, "+x", 0o755),
    (0o777, "go-w", 0o755),
    (0o000, "u=rwx,g=rx,o=", 0o750),
    (0o755, "a=r", 0o444),
    (0o640, "u=g", 0o440),
    (0o750, "o=g", 0o755),
    (0o764, "g-u", 0o704),
    (0o644, "a+X", 0o644),
    (0o744, "a+X", 0o755),
    (0o755, "g+s", 0o2755),
    (0o755, "+s", 0o6755),
    (0o755, "o+s", 0o755),
    (0o755, "u+t", 0o755),
    (0o755, "+t", 0o1755),
    (0o755, "o+t", 0o1755),
    (0o1777, "o=rx", 0o775),
    (0o1777, "ug=rwx", 0o1777),
    (0o6755, "u=rwx", 0o2755),
    (0o1777, "a=rwx", 0o777),
    (0o644, "u+r-w+x", 0o544),
    (0o644, "go=", 0o600),
    (0o600, "u+x,u-x,g+r", 0o640),
])
def test_symbolic_changes(cmodes, base, change, expected):
    assert cmodes.apply(base, cmodes.read_changes(change)) == expected


def test_directories_keep_setuid_and_setgid_unless_s_is_named(cmodes):
    changes = cmodes.read_changes("u=rwx,g=rx,o=")
    assert cmodes.apply(0o2755, changes, is_dir=True) == 0o2750
    assert cmodes.apply(0o2755, changes, is_dir=False) == 0o750
    assert cmodes.apply(0o2755, cmodes.read_changes("g-s"), is_dir=True) == 0o755
    assert cmodes.apply(0o644, cmodes.read_changes("a+X"), is_dir=True) == 0o755
    out = cmodes.expand('"drwxr-sr-x a=rx"')
    assert _row(out, "after") == "r-xr-sr-x  (2555)"


def test_a_change_applied_step_by_step(cmodes):
    out = cmodes.expand('"644 u+x,go-w"')
    assert out.splitlines()[:6] == [
        "Mode 644 with u+x,go-w",
        "  before    rw-r--r--  (644)",
        "  u+x       rwxr--r--  (744)  add execute for the owner",
        "  go-w      rwxr--r--  (744)  remove write from the group and others",
        "  after     rwxr--r--  (744)",
        "  adds      execute for owner",
    ]
    assert out.splitlines()[7] == "Mode 744 (rwxr--r--)"
    # Every operation of a clause shows in its one step.
    assert cmodes.expand('"644 u+r-w+x"').splitlines()[2] == (
        "  u+r-w+x   r-xr--r--  (544)  add read for the owner; remove write from the owner; "
        "add execute for the owner")
    # Spaces between clauses read like commas.
    assert cmodes.expand('"644 u+x go-w"').splitlines()[0] == "Mode 644 with u+x,go-w"


def test_a_change_without_who_notes_the_umask(cmodes):
    out = cmodes.expand('"644 +w"')
    assert any(note.startswith("with no u, g, o or a, chmod applies the change to everyone")
               for note in _notes(out))
    assert _row(out, "after") == "rw-rw-rw-  (666)"


def test_a_change_alone(cmodes):
    assert cmodes.expand("u+x,go-w").splitlines() == [
        "chmod u+x,go-w",
        "  u+x       add execute for the owner",
        "  go-w      remove write from the group and others",
        "  on 644    rwxr--r--  (744)",
        "  on 755    rwxr-xr-x  (755)",
        "Notes",
        '  - give a starting mode to see one result: @chmod:"644 u+x,go-w"',
    ]


@pytest.mark.parametrize("change, words", [
    ("u=g", "set the owner to the rights the group has now"),
    ("o-g", "take the rights the group has now away from others"),
    ("g+o", "give the group the rights others have now"),
    ("+t", "turn on the sticky bit"),
    ("g-s", "turn off setgid"),
    ("u+xs", "add execute for the owner; turn on setuid"),
    ("a+s", "turn on setuid and setgid"),
    ("o+s", "no change: s needs u or g"),
    ("u+t", "no change: t needs o"),
    ("g+st", "turn on setgid (t needs o, so that letter is ignored)"),
    ("go=", "clear every right of the group and others"),
    ("a=rX", "set everyone to exactly read and execute where some class can already execute (or on a "
             "directory)"),
    ("u=rws", "set the owner to exactly read and write, with setuid"),
    ("u+", "no change"),
    ("ug-w", "remove write from the owner and the group"),
    ("ugo-w", "remove write from everyone"),
])
def test_changes_in_words(cmodes, change, words):
    assert "; ".join(cmodes.describe_change(c) for c in cmodes.read_changes(change)) == words


def test_two_modes_are_compared(cmodes):
    out = cmodes.expand('"100644 100755"')
    assert out.splitlines()[:7] == [
        "Mode 644 -> 755",
        "  before    rw-r--r--  (644)",
        "  after     rwxr-xr-x  (755)",
        "  adds      execute for owner, group and others",
        "Notes",
        "  - this is the change git shows as old mode / new mode when a file's execute bit flips",
        "",
    ]
    assert out.splitlines()[7] == "Mode 755 (rwxr-xr-x), a regular file"
    both = cmodes.expand('"4750 0644"')
    assert _row(both, "adds") == "read for others"
    assert _row(both, "removes") == "execute for owner and group; setuid"
    assert _row(cmodes.expand('"644 644"'), "no") == "change"
    assert "the file type changes too: regular file -> directory" in _notes(
        cmodes.expand('"-rwxr-xr-x drwxr-xr-x"'))


def test_umask(cmodes):
    assert cmodes.expand('"umask 022"').splitlines() == [
        "umask 022",
        "  removes   write for group and others",
        "  files     rw-r--r--  (644)  new files (programs ask for 666)",
        "  dirs      rwxr-xr-x  (755)  new directories (mkdir asks for 777)",
        "Notes",
        "  - a umask only takes rights away: programs that ask for less (600 for a key, for example) get less",
    ]
    for spelling in ("umask=022", "umask:0022", "UMASK 22", "umask022"):
        assert cmodes.expand(spelling).splitlines()[0] == "umask 022"
    assert "new files and directories are private to their owner" in _notes(cmodes.expand('"umask 077"'))
    assert "new files and directories are writable by everyone on the machine" in _notes(
        cmodes.expand('"umask 000"'))
    assert any(note.startswith("the execute bits only matter") for note in _notes(cmodes.expand('"umask 011"')))
    assert "the umask also blocks the owner's own rights, which is unusual" in _notes(
        cmodes.expand('"umask 277"'))
    assert _row(cmodes.expand('"umask 027"'), "removes") == "write for group; read, write and execute for others"


@pytest.mark.parametrize("value, message", [
    ("", "no mode given"),
    ('""', "no mode given"),
    ("788", "8 is not an octal digit"),
    ("12345670", "has 8 digits"),
    ("12345678", "8 is not an octal digit"),
    ("1234567", "has 7 digits"),
    ("070644", "7 is not a file type"),
    ("rwxr-xr-z", "is not a mode"),
    ("rwxs-xr-x", "a letter is in the wrong place"),
    ("-rwxr-xr-xx", "a letter is in the wrong place"),
    ("drwxr-xr-x+@", "a letter is in the wrong place"),
    ("u+q", "is not a mode"),
    ('"644 u+q"', "is not a chmod change"),
    ('"644 755 u+x"', "is not a chmod change"),
    ('"u+x 644"', "is not a mode"),
    ('"umask 1022"', "a umask covers only the 9 permission bits"),
    ("7" * 201, "up to 200 are read"),
])
def test_bad_values_raise_with_a_clear_message(cmodes, value, message):
    with pytest.raises(ValueError, match=message):
        cmodes.expand(value)


def test_preview(cmodes):
    assert cmodes.preview("755") == [("755", "755 = rwxr-xr-x", "file mode")]
    assert cmodes.preview("drwxr-sr-x") == [("drwxr-sr-x", "2755 = drwxr-sr-x, directory", "file mode")]
    assert cmodes.preview("644 u+x,go-w") == [('"644 u+x,go-w"', "644 u+x,go-w -> 744 (rwxr--r--)", "file mode")]
    assert cmodes.preview('"644 755"') == [('"644 755"', "644 -> 755: adds execute for owner, group and others",
                                           "file mode")]
    assert cmodes.preview("umask 027") == [('"umask 027"', "umask 027: new files 640, new directories 750",
                                           "file mode")]
    assert cmodes.preview("go-w") == [("go-w", "remove write from the group and others", "file mode")]
    assert cmodes.preview("") == [
        ("755", "755 = rwxr-xr-x", "example"),
        ('"644 u+x"', "644 u+x -> 744 (rwxr--r--)", "example"),
        ('"umask 022"', "umask 022: new files 644, new directories 755", "example"),
    ]
    for unreadable in ("78", "75x", "644 u+q", "160000 u+x"):
        assert cmodes.preview(unreadable) == []


def _random_change(rng):
    clauses = []
    for _ in range(rng.randint(1, 3)):
        who = "".join(rng.sample("ugoa", rng.randint(0, 2)))
        actions = ""
        for _ in range(rng.randint(1, 2)):
            op = rng.choice("+-=")
            if rng.random() < 0.15:
                actions += op + rng.choice("ugo")
            else:
                actions += op + "".join(rng.sample("rwxXst", rng.randint(0, 3)))
        clauses.append(who + actions)
    return ",".join(clauses)


@pytest.mark.skipif(not LINUX or not shutil.which("chmod"), reason="compares with GNU chmod on Linux")
def test_changes_agree_with_gnu_chmod(cmodes, tmp_path):
    rng = random.Random(644)
    target_file = tmp_path / "file"
    target_file.write_text("", encoding="utf-8")
    target_dir = tmp_path / "dir"
    target_dir.mkdir()
    checked = 0
    for _ in range(400):
        change = _random_change(rng)
        base = rng.randrange(0o10000)
        for target, is_dir in ((target_file, False), (target_dir, True)):
            os.chmod(target, base)
            start = stat.S_IMODE(os.stat(target).st_mode)
            if start != base:  # the kernel would not take the base mode as is
                continue
            done = subprocess.run(["sh", "-c", 'umask 0; exec chmod -- "$1" "$2"', "sh", change, str(target)],
                                  capture_output=True, text=True)
            assert done.returncode == 0, (change, done.stderr)
            got = stat.S_IMODE(os.stat(target).st_mode)
            assert cmodes.apply(base, cmodes.read_changes(change), is_dir=is_dir) == got, (oct(base), change, is_dir)
            checked += 1
    assert checked > 600


@pytest.mark.skipif(os.name == "nt", reason="Windows has no POSIX umask")
@pytest.mark.parametrize("mask", [0o000, 0o002, 0o022, 0o027, 0o077, 0o011, 0o277])
def test_umask_agrees_with_real_files(cmodes, tmp_path, mask):
    old = os.umask(mask)
    try:
        fd = os.open(tmp_path / "file", os.O_CREAT | os.O_WRONLY, 0o666)
        os.close(fd)
        os.mkdir(tmp_path / "dir", 0o777)
    finally:
        os.umask(old)
    out = cmodes.expand(f'"umask {mask:03o}"')
    assert _row(out, "files").split("  ")[1] == f"({stat.S_IMODE(os.stat(tmp_path / 'file').st_mode):03o})"
    assert _row(out, "dirs").split("  ")[1] == f"({stat.S_IMODE(os.stat(tmp_path / 'dir').st_mode):03o})"


@pytest.fixture
def hermes_api(monkeypatch):
    """Stand-ins for the two classes the plugin takes from agent.context_references."""
    module = types.ModuleType("agent.context_references")

    class ContextCompletionItem:
        def __init__(self, text, display="", meta=""):
            self.text, self.display, self.meta = text, display or text, meta

    class ContextReferenceProvider:
        prefix = ""
        description = ""

    module.ContextCompletionItem = ContextCompletionItem
    module.ContextReferenceProvider = ContextReferenceProvider
    monkeypatch.setitem(sys.modules, "agent", types.ModuleType("agent"))
    monkeypatch.setitem(sys.modules, "agent.context_references", module)
    return module


def test_provider_expands_and_suggests(chmod_plugin, hermes_api):
    provider = chmod_plugin.make_provider()
    assert isinstance(provider, hermes_api.ContextReferenceProvider)
    assert provider.prefix == "chmod" and provider.description
    out = asyncio.run(provider.expand("750"))
    assert "  group     read, execute" in out
    items = asyncio.run(provider.autocomplete("750", limit=5))
    assert [(i.text, i.display, i.meta) for i in items] == [("750", "750 = rwxr-x---", "file mode")]
    assert len(asyncio.run(provider.autocomplete("", limit=2))) == 2
    with pytest.raises(ValueError, match="no mode given"):
        asyncio.run(provider.expand(""))


def test_register_registers_one_context_reference(chmod_plugin, hermes_api):
    calls = []

    class Ctx:
        def register_context_reference(self, provider):
            calls.append(provider)

    chmod_plugin.register(Ctx())
    assert len(calls) == 1 and calls[0].prefix == "chmod"
