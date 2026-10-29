"""File permission modes for ``@chmod:``: what a mode, a chmod change or a umask means. Nothing is read.

Standard library only (``re``): nothing here imports Hermes, so it runs unchanged under the admission
probe and in unit tests. The mode bits are worked out here by hand, not with the ``stat`` module, so
the tests can hold them against ``stat.filemode`` and against a real ``chmod`` as an independent check.

Symbolic changes (``u+x,go-w``) follow GNU chmod (gnulib ``modechange.c``) with a umask of 0.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

MAX_TARGET_CHARS = 200

SETUID, SETGID, STICKY = 0o4000, 0o2000, 0o1000
ALL_BITS = 0o7777
_X_ALL = 0o111
_CLASSES = (("owner", 6), ("group", 3), ("others", 0))  # name, shift of its rwx bits
_WHO_BITS = {"u": SETUID | 0o700, "g": SETGID | 0o070, "o": STICKY | 0o007, "a": ALL_BITS}

# File types, as the top bits of st_mode (and git's tree entry modes) and as ls -l's first letter.
_TYPES = {
    0o14: ("s", "socket"),
    0o12: ("l", "symbolic link"),
    0o10: ("-", "regular file"),
    0o06: ("b", "block device"),
    0o04: ("d", "directory"),
    0o02: ("c", "character device"),
    0o01: ("p", "named pipe (FIFO)"),
}
_TYPE_BY_LETTER = {letter: (code, name) for code, (letter, name) in _TYPES.items()}
_GITLINK = 0o16  # 160000: how git records a submodule
_SUFFIXES = {
    "+": "the + after the bits means an access control list (ACL) grants more than these bits show; "
         "getfacl lists it",
    ".": "the . after the bits means the file has an SELinux security context; ls -Z shows it",
    "@": "the @ after the bits means the file has extended attributes (macOS); xattr -l lists them",
}

EXAMPLES = ("755", "644 u+x", "umask 022")

_OCTAL = re.compile(r"^(?:0o)?([0-7]{1,6})$", re.IGNORECASE)
_LS = re.compile(r"^([-dlcbps]?)([r-][w-][xsS-][r-][w-][xsS-][r-][w-][xtT-])([+.@]?)$")
_CLAUSE = re.compile(r"^([ugoa]*)((?:[-+=](?:[ugo]|[rwxXst]*))+)$")
_ACTION = re.compile(r"([-+=])([ugo]|[rwxXst]*)")
_LOOSE_LS = re.compile(r"^[-dlcbps]?[rwxsStT-]{9}[-rwxsStT+.@]{0,3}$")
_UMASK = re.compile(r"^umask[\s=:]*([0-7]{1,4})$", re.IGNORECASE)


class Mode:
    """Permission bits (setuid, setgid, sticky and rwx for three classes), with the file type if known."""

    def __init__(self, bits: int, ftype: Optional[str] = None, suffix: str = "", written: str = "",
                 gitlink: bool = False):
        self.bits = bits & ALL_BITS
        self.ftype = ftype      # an ls letter ("-", "d", ...) or None when the text did not say
        self.suffix = suffix    # "+", "." or "@" after ls -l's bits
        self.written = written
        self.gitlink = gitlink

    @property
    def is_dir(self) -> bool:
        return self.ftype == "d"

    @property
    def octal(self) -> str:
        return _octal(self.bits)


def symbolic(bits: int) -> str:
    """The nine letters ls -l shows for these bits (rwxr-xr-x, with s, S, t and T)."""
    out = []
    for name, shift in _CLASSES:
        part = bits >> shift
        out.append("r" if part & 4 else "-")
        out.append("w" if part & 2 else "-")
        special = {"owner": SETUID, "group": SETGID, "others": STICKY}[name] & bits
        letter = "t" if name == "others" else "s"
        if special:
            out.append(letter if part & 1 else letter.upper())
        else:
            out.append("x" if part & 1 else "-")
    return "".join(out)


def _read_ls(match) -> Mode:
    ftype, letters, suffix = match.groups()
    bits = 0
    for index, (name, shift) in enumerate(_CLASSES):
        r, w, x = letters[3 * index: 3 * index + 3]
        bits |= ((r == "r") << 2 | (w == "w") << 1 | (x in "xst")) << shift
        if x in "sStT":
            bits |= {"owner": SETUID, "group": SETGID, "others": STICKY}[name]
    return Mode(bits, ftype or None, suffix, match.group(0))


def read_mode(text: str) -> Optional[Mode]:
    """A mode from octal (755, 0755, 4755, 0o644, 100644) or ls -l text (rwxr-xr-x, -rw-r--r--@)."""
    match = _OCTAL.match(text)
    if match:
        digits = match.group(1)
        value = int(digits, 8)
        kind = value >> 12  # 0 for a plain mode (755, 4755, 00644); a file type from 5 digits on
        if kind == 0:
            return Mode(value, written=text)
        if kind == _GITLINK:
            return Mode(value, written=text, gitlink=True)
        if kind not in _TYPES:
            raise ValueError(f"{text!r}: {kind:o} is not a file type (st_mode types are 01, 02, 04, 06, "
                             f"10, 12 and 14, as in 100644 for a file or 040755 for a directory)")
        return Mode(value, _TYPES[kind][0], written=text)
    match = _LS.match(text)
    if match:
        return _read_ls(match)
    return None


class Change:
    """One operation of a symbolic chmod clause, as gnulib's mode_compile records it."""

    def __init__(self, clause: str, who: str, op: str, perms: str, index: int = 0):
        self.clause, self.who, self.op, self.perms = clause, who, op, perms
        self.index = index  # which comma-separated clause it came from
        self.affected = 0
        for letter in who:
            self.affected |= _WHO_BITS[letter]
        self.copy = perms in ("u", "g", "o")
        self.if_any_x = "X" in perms
        self.value = 0
        if self.copy:
            self.value = {"u": 0o700, "g": 0o070, "o": 0o007}[perms]
        else:
            for letter in perms:
                self.value |= {"r": 0o444, "w": 0o222, "x": 0o111, "X": 0,
                               "s": SETUID | SETGID, "t": STICKY}[letter]
        # gnulib narrows this to the affected classes; for the one use below (which of setuid and
        # setgid a directory keeps) the narrower and the plain value give the same result.
        self.mentioned = self.value


def read_changes(text: str) -> Optional[List[Change]]:
    """``u+x,go-w`` as a list of changes, or None if the text is not a symbolic chmod mode."""
    changes = []
    for index, clause in enumerate(text.split(",")):
        match = _CLAUSE.match(clause)
        if not match:
            return None
        who, actions = match.groups()
        for op, perms in _ACTION.findall(actions):
            changes.append(Change(clause, who, op, perms, index))
    return changes or None


def apply(bits: int, changes: List[Change], *, is_dir: bool = False) -> int:
    """GNU chmod's mode_adjust with a umask of 0."""
    new = bits & ALL_BITS
    for change in changes:
        omit = (SETUID | SETGID if is_dir else 0) & ~change.mentioned
        value = change.value
        if change.copy:
            value &= new
            value |= ((0o444 if value & 0o444 else 0) | (0o222 if value & 0o222 else 0)
                      | (0o111 if value & 0o111 else 0))
        elif change.if_any_x and (new & _X_ALL or is_dir):
            value |= _X_ALL
        value &= (change.affected or ALL_BITS) & ~omit
        if change.op == "=":
            preserved = ((~change.affected & ALL_BITS) if change.affected else 0) | omit
            new = (new & preserved) | value
        elif change.op == "+":
            new |= value
        else:
            new &= ~value
    return new & ALL_BITS


def _join(words: List[str]) -> str:
    if len(words) <= 1:
        return "".join(words)
    return ", ".join(words[:-1]) + " and " + words[-1]


def _who_words(who: str) -> str:
    if not who or "a" in who or set(who) >= {"u", "g", "o"}:
        return "everyone"
    return _join([{"u": "the owner", "g": "the group", "o": "others"}[letter] for letter in "ugo" if letter in who])


def describe_change(change: Change) -> str:
    """One change in words: ``go-w`` -> ``remove write from group and others``."""
    target = _who_words(change.who)
    if change.copy:
        source = {"u": "the owner", "g": "the group", "o": "others"}[change.perms]
        has = "have" if change.perms == "o" else "has"
        return {"=": f"set {target} to the rights {source} {has} now",
                "+": f"give {target} the rights {source} {has} now",
                "-": f"take the rights {source} {has} now away from {target}"}[change.op]
    words = [word for letter, word in (("r", "read"), ("w", "write"), ("x", "execute"),
                                       ("X", "execute where some class can already execute (or on a directory)"))
             if letter in change.perms]
    who = set(change.who or "a")
    specials = []
    if "s" in change.perms:
        specials += (["setuid"] if who & {"u", "a"} else []) + (["setgid"] if who & {"g", "a"} else [])
    if "t" in change.perms and who & {"o", "a"}:
        specials.append("the sticky bit")
    ignored = ("s" in change.perms and not who & {"u", "g", "a"}) or ("t" in change.perms and not who & {"o", "a"})
    if change.op == "=":
        out = f"set {target} to exactly {_join(words)}" if words else f"clear every right of {target}"
        if specials:
            out += f", with {_join(specials)}"
    else:
        parts = []
        if words:
            parts.append(f"{'add' if change.op == '+' else 'remove'} {_join(words)} "
                         f"{'for' if change.op == '+' else 'from'} {target}")
        if specials:
            parts.append(f"turn {'on' if change.op == '+' else 'off'} {_join(specials)}")
        out = "; ".join(parts) if parts else "no change"
    if ignored:
        why = "s needs u or g" if "s" in change.perms and not who & {"u", "g", "a"} else "t needs o"
        out = f"no change: {why}" if out == "no change" else f"{out} ({why}, so that letter is ignored)"
    return out


def rights(bits: int, shift: int, is_dir: bool) -> str:
    part = (bits >> shift) & 7
    if is_dir:
        names = (("list names", 4), ("add, rename and delete entries", 2), ("enter", 1))
    else:
        names = (("read", 4), ("write", 2), ("execute", 1))
    words = [word for word, bit in names if part & bit]
    return ", ".join(words) if words else "nothing"


def _special_notes(mode: Mode) -> List[str]:
    bits, kind = mode.bits, mode.ftype
    notes = []
    if bits & SETUID:
        if mode.is_dir:
            notes.append("setuid on a directory is ignored by Linux and macOS")
        else:
            notes.append("setuid: the program runs with its owner's rights, not those of the user who starts it "
                         "(root-owned setuid programs, like passwd, run as root)")
            if not bits & 0o100:
                notes.append("setuid is set but the owner has no execute (ls shows S), so it does nothing")
    if bits & SETGID:
        if mode.is_dir:
            notes.append("setgid on a directory: new files and directories inside take its group "
                         "(new directories also get setgid)")
        elif kind is None:
            notes.append("setgid: a program runs with the file's group; on a directory, new entries inside take "
                         "its group")
        else:
            notes.append("setgid: the program runs with the file's group")
        if not bits & 0o010 and not mode.is_dir:
            notes.append("setgid is set but the group has no execute (ls shows S), so it does not apply when the "
                         "program runs")
    if bits & STICKY:
        if mode.is_dir or kind is None:
            notes.append("sticky on a directory: only a file's owner, the directory's owner or root can delete or "
                         "rename files in it, as on /tmp (1777)" + ("" if mode.is_dir else "; on files Linux and "
                                                                     "macOS ignore it"))
        else:
            notes.append("sticky on a file is ignored by Linux and macOS")
        if not bits & 0o001:
            notes.append("ls shows T because others have no execute; the sticky bit still applies to a directory")
    return notes


def notes(mode: Mode) -> List[str]:
    """What is worth saying about a mode, beyond its rows."""
    bits, is_dir, out = mode.bits, mode.is_dir, []
    perm = bits & 0o777
    if mode.gitlink:
        return ["160000 is how git records a submodule (a commit of another repository), not a file mode"]
    if mode.ftype == "l":
        out.append("a symbolic link's own mode is not checked on Linux (it is always rwxrwxrwx there); "
                   "access depends on its target")
    if perm == 0:
        out.append("no one but root can read, change or run it")
    elif perm == 0o777:
        out.append("everyone can read, change and " + ("enter it" if is_dir else "run it"))
    elif bits & 0o002:
        if is_dir:
            out.append("others can write: anyone on the machine can create, rename and delete entries in it"
                       + ("" if bits & STICKY else " (the sticky bit, as on /tmp, would stop them deleting "
                                                  "others' files)"))
        else:
            out.append("others can write: anyone on the machine can change it")
    if perm & 0o077 == 0 and perm & 0o400:
        out.append("only the owner (and root) can use it; ssh wants this (600 or 400) for private keys"
                   if not is_dir else "only the owner (and root) can use it")
    # The kernel uses the first class that matches the user: an owner gets only the owner bits.
    for index, (low, low_shift) in enumerate(_CLASSES[:2]):
        later = _CLASSES[index + 1:]
        extra = 0
        for _name, shift in later:
            extra |= (bits >> shift) & 7
        extra &= ~((bits >> low_shift) & 7)
        if extra:
            words = [word for word, bit in (("read", 4), ("write", 2), ("execute", 1)) if extra & bit]
            holders = [("the group" if name == "group" else name) for name, shift in later
                       if (bits >> shift) & 7 & extra]
            who = "the " + low
            out.append(f"{_join(holders)} {'has' if holders == ['the group'] else 'have'} {_join(words)}, which "
                       f"{who} lacks: only the first class that matches a user counts, so {who} cannot use "
                       f"those rights")
    if not is_dir and mode.ftype in (None, "-"):
        for name, shift in _CLASSES:
            part = (bits >> shift) & 7
            if part & 1 and not part & 4:
                out.append(f"{name} can execute but not read: a compiled program runs, a script does not "
                           f"(its interpreter has to read it)")
                break
        if bits & 0o400 and not bits & 0o200:
            out.append("read-only for the owner too: editors warn before saving; whether it can be deleted "
                       "depends on the directory, not on the file")
    if is_dir:
        for name, shift in _CLASSES:
            part = (bits >> shift) & 7
            if part & 1:
                continue
            if part & 2:
                out.append(f"{name}: write without execute on a directory does nothing; creating, renaming "
                           f"and deleting entries needs execute too")
                break
            if part & 4:
                out.append(f"{name} can list the names but not enter the directory or open anything in it")
                break
    if mode.ftype == "-" and mode.written.isdigit() and len(mode.written) >= 5 and perm not in (0o644, 0o755):
        out.append("git records only 100644 or 100755 for a file; other bits are not kept")
    out.extend(_special_notes(mode))
    if mode.suffix:
        out.append(_SUFFIXES[mode.suffix])
    return out


def _type_name(mode: Mode) -> Optional[str]:
    if mode.gitlink:
        return "git submodule"
    if mode.ftype is None:
        return None
    return _TYPE_BY_LETTER[mode.ftype][1]


def _ls(mode: Mode) -> str:
    return (mode.ftype or "-") + symbolic(mode.bits) + mode.suffix


def mode_rows(mode: Mode) -> List[str]:
    rows = [f"  octal     {mode.octal}"]
    if mode.written.isdigit() and len(mode.written) >= 5:
        rows.append(f"  st_mode   {mode.written} (file type and permission bits, as stat and git write them)")
    if mode.ftype is None and not mode.gitlink:
        rows.append(f"  ls -l     -{symbolic(mode.bits)} for a file, d{symbolic(mode.bits)} for a directory")
    else:
        rows.append(f"  ls -l     {_ls(mode)}")
    if mode.gitlink:
        return rows
    for name, shift in _CLASSES:
        rows.append(f"  {name:<9} {rights(mode.bits, shift, mode.is_dir)}")
    specials = [word for word, bit in (("setuid", SETUID), ("setgid", SETGID), ("sticky", STICKY))
                if mode.bits & bit]
    rows.append(f"  special   {', '.join(specials) if specials else 'none'}")
    return rows


def _with_notes(lines: List[str], extra: List[str]) -> List[str]:
    if extra:
        lines.append("Notes")
        lines.extend(f"  - {note}" for note in extra)
    return lines


def describe(mode: Mode) -> List[str]:
    kind = _type_name(mode)
    head = f"Mode {mode.octal} ({symbolic(mode.bits)})" + (f", a {kind}" if kind else "")
    return _with_notes([head] + mode_rows(mode), notes(mode))


def _diff(before: int, after: int) -> List[str]:
    rows = []
    for verb, bits in (("adds", after & ~before), ("removes", before & ~after)):
        if not bits:
            continue
        parts = []
        for name, shift in _CLASSES:
            words = [word for word, bit in (("read", 4), ("write", 2), ("execute", 1)) if (bits >> shift) & bit]
            if words:
                parts.append((name, _join(words)))
        # Same rights for several classes read as one phrase: "execute for owner, group and others".
        grouped = {}
        for name, words in parts:
            grouped.setdefault(words, []).append(name)
        phrases = [f"{words} for {_join(names)}" for words, names in grouped.items()]
        phrases += [word for word, bit in (("setuid", SETUID), ("setgid", SETGID), ("sticky", STICKY)) if bits & bit]
        rows.append(f"  {verb:<9} {'; '.join(phrases)}")
    return rows or ["  no change"]


def _octal(bits: int) -> str:
    return f"{bits:o}".zfill(4 if bits > 0o777 else 3)


def _state(bits: int) -> str:
    return f"{symbolic(bits)}  ({_octal(bits)})"


def compare(before: Mode, after: Mode) -> List[str]:
    lines = [f"Mode {before.octal} -> {after.octal}",
             f"  before    {_state(before.bits)}",
             f"  after     {_state(after.bits)}"]
    lines += _diff(before.bits, after.bits)
    extra = []
    if before.ftype != after.ftype and before.ftype and after.ftype:
        extra.append(f"the file type changes too: {_type_name(before)} -> {_type_name(after)}")
    if before.ftype == after.ftype == "-" and {before.bits & 0o777, after.bits & 0o777} == {0o644, 0o755}:
        extra.append("this is the change git shows as old mode / new mode when a file's execute bit flips")
    return _with_notes(lines, extra) + [""] + describe(after)


def _clauses(changes: List[Change]) -> List[Tuple[str, List[Change]]]:
    """Changes grouped back into the comma-separated clauses they came from, in order."""
    out: List[Tuple[str, List[Change]]] = []
    for change in changes:
        if out and out[-1][1][-1].index == change.index:
            out[-1][1].append(change)
        else:
            out.append((change.clause, [change]))
    return out


def _change_notes(changes: List[Change]) -> List[str]:
    out = []
    if any(not change.who for change in changes):
        out.append("with no u, g, o or a, chmod applies the change to everyone but leaves out bits your umask "
                   "blocks; this assumes a umask of 0 (umask 022 keeps +w to the owner)")
    if any(change.if_any_x for change in changes):
        out.append("X adds execute only to directories and to files some class can already execute")
    return out


def apply_text(base: Mode, changes: List[Change], text: str) -> List[str]:
    lines = [f"Mode {base.octal} with {text}", f"  before    {_state(base.bits)}"]
    width = max(9, max(len(change.clause) for change in changes))
    bits = base.bits
    for clause, group in _clauses(changes):
        bits = apply(bits, group, is_dir=base.is_dir)
        lines.append(f"  {clause:<{width}} {_state(bits)}  {'; '.join(describe_change(c) for c in group)}")
    result = Mode(bits, base.ftype, written="")
    lines.append(f"  {'after':<{width}} {_state(bits)}")
    lines += _diff(base.bits, bits)
    lines = _with_notes(lines, _change_notes(changes))
    return lines + [""] + describe(result)


def changes_text(changes: List[Change], text: str) -> List[str]:
    lines = [f"chmod {text}"]
    width = max(9, max(len(change.clause) for change in changes))
    for clause, group in _clauses(changes):
        lines.append(f"  {clause:<{width}} {'; '.join(describe_change(c) for c in group)}")
    for base in (0o644, 0o755):
        lines.append(f"  {'on ' + format(base, 'o'):<{width}} {_state(apply(base, changes))}")
    extra = _change_notes(changes)
    extra.append(f'give a starting mode to see one result: @chmod:"644 {text}"')
    return _with_notes(lines, extra)


def umask_text(mask: int) -> List[str]:
    files, dirs = 0o666 & ~mask, 0o777 & ~mask
    blocked = []
    for name, shift in _CLASSES:
        words = [word for word, bit in (("read", 4), ("write", 2), ("execute", 1)) if (mask >> shift) & bit]
        if words:
            blocked.append((name, _join(words)))
    grouped = {}
    for name, words in blocked:
        grouped.setdefault(words, []).append(name)
    phrase = "; ".join(f"{words} for {_join(names)}" for words, names in grouped.items()) or "nothing"
    lines = [f"umask {mask:03o}",
             f"  removes   {phrase}",
             f"  files     {_state(files)}  new files (programs ask for 666)",
             f"  dirs      {_state(dirs)}  new directories (mkdir asks for 777)"]
    extra = []
    if mask & 0o002 == 0:
        extra.append("new files and directories are writable by everyone on the machine")
    if mask & 0o077 == 0o077:
        extra.append("new files and directories are private to their owner")
    if mask & 0o111 and not mask & 0o666:
        extra.append("the execute bits only matter for directories and for files made with execute asked for "
                     "(a compiler's output, for example)")
    if mask & 0o700:
        extra.append("the umask also blocks the owner's own rights, which is unusual")
    extra.append("a umask only takes rights away: programs that ask for less (600 for a key, for example) "
                 "get less")
    return _with_notes(lines, extra)


def _unquote(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'`":
        text = text[1:-1].strip()
    return text


def _parse_mode(text: str) -> Mode:
    mode = read_mode(text)
    if mode is None:
        raise ValueError(_mode_error(text))
    return mode


def _mode_error(text: str) -> str:
    if text.isdigit():
        bad = sorted(set(text) - set("01234567"))
        if bad:
            return f"{text!r} is not octal: {', '.join(bad)} is not an octal digit (0-7)"
        return f"{text!r} has {len(text)} digits; a mode has 3 or 4 (755, 4755), or 6 with the file type (100644)"
    if _LOOSE_LS.match(text):
        return (f"{text!r} looks like ls -l text but a letter is in the wrong place: each class is r or -, "
                f"then w or -, then x, s or S (t or T for others) or -")
    return (f"{text!r} is not a mode: try 755, 0644, rwxr-xr-x, -rw-r--r--, u+x,go-w, \"644 u+x\" "
            f"or \"umask 022\"")


def expand(target: str) -> str:
    """What ``@chmod:<target>`` puts into the message."""
    text = _unquote(target)
    if not text:
        raise ValueError('no mode given; try @chmod:755, @chmod:-rw-r--r-- or @chmod:"644 u+x"')
    if len(text) > MAX_TARGET_CHARS:
        raise ValueError(f"the value is {len(text)} characters; up to {MAX_TARGET_CHARS} are read")
    umask = _UMASK.match(text)
    if umask:
        digits = umask.group(1)
        if len(digits) == 4 and digits[0] != "0":
            raise ValueError(f"umask {digits}: a umask covers only the 9 permission bits, so a 4-digit one "
                             f"starts with 0")
        return "\n".join(umask_text(int(digits, 8)))
    tokens = text.split()
    if len(tokens) == 1:
        mode = read_mode(text)
        if mode is not None:
            return "\n".join(describe(mode))
        changes = None if _LOOSE_LS.match(text) else read_changes(text)
        if changes is not None:
            return "\n".join(changes_text(changes, text))
        raise ValueError(_mode_error(text))
    base = _parse_mode(tokens[0])
    if len(tokens) == 2:
        other = read_mode(tokens[1])
        if other is not None:
            return "\n".join(compare(base, other))
    rest = ",".join(tokens[1:])
    changes = read_changes(rest)
    if changes is None:
        raise ValueError(f"{' '.join(tokens[1:])!r} is not a chmod change: write it like u+x, go-w, a=r, "
                         f"g+s or u=g (several joined with commas)")
    if base.gitlink:
        raise ValueError("160000 is a git submodule entry; it has no permission bits to change")
    return "\n".join(apply_text(base, changes, rest))


def summary(text: str) -> str:
    """A one-line preview: ``755 = rwxr-xr-x``, ``644 u+x -> 744 (rwxr--r--)``."""
    text = _unquote(text)
    umask = _UMASK.match(text)
    if umask:
        mask = int(umask.group(1), 8)
        return f"umask {mask:03o}: new files {0o666 & ~mask:03o}, new directories {0o777 & ~mask:03o}"
    tokens = text.split()
    if len(tokens) == 1:
        mode = read_mode(text)
        if mode is not None:
            kind = _type_name(mode)
            return f"{mode.octal} = {_ls(mode) if mode.ftype else symbolic(mode.bits)}" + (f", {kind}" if kind else "")
        changes = None if _LOOSE_LS.match(text) else read_changes(text)
        if changes is not None:
            return "; ".join(describe_change(change) for change in changes)
        raise ValueError(_mode_error(text))
    base = _parse_mode(tokens[0])
    if len(tokens) == 2:
        other = read_mode(tokens[1])
        if other is not None:
            return f"{base.octal} -> {other.octal}: " + "; ".join(" ".join(row.split()) for row in _diff(base.bits, other.bits))
    changes = read_changes(",".join(tokens[1:]))
    if changes is None or base.gitlink:
        raise ValueError("not a chmod change")
    after = apply(base.bits, changes, is_dir=base.is_dir)
    return f"{base.octal} {','.join(tokens[1:])} -> {_octal(after)} ({symbolic(after)})"


def preview(query: str) -> List[Tuple[str, str, str]]:
    """Suggestions for the TUI and desktop app: (inserted text, display, meta)."""
    text = _unquote(query)
    if not text:
        return [(f'"{example}"' if " " in example else example, summary(example), "example")
                for example in EXAMPLES]
    try:
        shown = summary(text)
    except ValueError:
        return []
    inserted = f'"{text}"' if " " in text else text
    return [(inserted, shown, "file mode")]
