"""Tiny stdlib-only HTML query helper for tests — assert on structure, not raw markup strings.

Rationale: pinning exact tag text (`assert '<details class="advcfg" id="x" open ...>' in body`) breaks
on any harmless attribute reorder / class rename. Query by id / name / tag instead and assert on the
facts that matter (an element exists, it is `open`, a field's rendered default). bs4/lxml are not
installed, so this wraps the stdlib `html.parser`.

    doc = parse(body)
    doc.by_id("stack-daemon-params-meshcom").has_attr("open")   # panel open when a URL requires it
    doc.field_default("dp_MODE") == "FSK"                        # rendered default / selected option
    doc.present("webserver-row")                                 # element exists
"""
from __future__ import annotations

from html.parser import HTMLParser


class _El:
    __slots__ = ("tag", "attrs")

    def __init__(self, tag, attrs):
        self.tag = tag
        self.attrs = dict(attrs)          # boolean attrs (e.g. `open`, `selected`) map to None

    def has_attr(self, name):
        return name in self.attrs

    def __getitem__(self, name):
        return self.attrs.get(name)


class _Doc(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._els = []                    # every start tag, in document order
        self._sel_name = None             # current <select name=...> being parsed
        self._sel_default = {}            # select name -> selected option value (or first option)

    def handle_starttag(self, tag, attrs):
        el = _El(tag, attrs)
        self._els.append(el)
        if tag == "select":
            self._sel_name = el["name"]
        elif tag == "option" and self._sel_name is not None:
            val = el["value"]
            if "selected" in el.attrs or self._sel_name not in self._sel_default:
                self._sel_default[self._sel_name] = val   # selected wins; else first option

    def handle_endtag(self, tag):
        if tag == "select":
            self._sel_name = None

    # --- queries ---
    def by_id(self, id_):
        for el in self._els:
            if el["id"] == id_:
                return el
        return None

    def present(self, id_):
        return self.by_id(id_) is not None

    def find(self, tag, **attrs):
        out = []
        for el in self._els:
            if el.tag != tag:
                continue
            if all(el[k] == v for k, v in attrs.items()):
                out.append(el)
        return out

    def field_default(self, name):
        """The rendered default of a form control: an <input>'s `value`, or a <select>'s selected
        <option> value (falling back to its first option)."""
        if name in self._sel_default:
            return self._sel_default[name]
        for el in self._els:
            if el.tag == "input" and el["name"] == name:
                return el["value"]
        return None


def parse(body):
    if isinstance(body, bytes):
        body = body.decode()
    doc = _Doc()
    doc.feed(body)
    return doc
