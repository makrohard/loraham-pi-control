"""Tiny stdlib-only HTML query helper for tests — assert on structure, not raw markup strings.

Rationale: pinning exact tag text (`assert '<details class="advcfg" id="x" open ...>' in body`) breaks
on any harmless attribute reorder / class rename. Query by id / name / tag / class token instead and
assert on the facts that matter (an element exists, it is `open`, a field's rendered default, the
text a row shows, what sits inside which element). bs4/lxml are not installed, so this wraps the
stdlib `html.parser`.

    doc = parse(body)
    doc.by_id("stack-daemon-params-meshcom").has_attr("open")   # panel open when a URL requires it
    doc.field_default("dp_MODE") == "FSK"                        # rendered default / selected option
    doc.present("webserver-row")                                 # element exists
    doc.find("a", class_="update-link")[0]["href"]              # class TOKEN match, attribute read
    row = doc.within(doc.by_id("stackrow-daemon"))              # scope every query to a subtree
    row.find("span", class_="pill") ; row.text                   # …and the text it renders
    doc.index(a) < doc.index(b)                                  # document order

Scoping is by nesting: an element's subtree runs from its start tag to its matching end tag (void
tags such as <input> and <br> have none). Text is the concatenated character data under an element.
"""
from __future__ import annotations

from html.parser import HTMLParser

_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param",
         "source", "track", "wbr"}


def _join(runs):
    return " ".join(" ".join(run.split()) for run in runs if run.strip())


class _El:
    __slots__ = ("tag", "attrs", "index", "end", "_text")

    def __init__(self, tag, attrs, index):
        self.tag = tag
        self.attrs = dict(attrs)          # boolean attrs (e.g. `open`, `selected`) map to None
        self.index = index                # position among all start tags, document order
        self.end = index                  # index of the last element inside this one (set on close)
        self._text = []

    def has_attr(self, name):
        return name in self.attrs

    def __getitem__(self, name):
        return self.attrs.get(name)

    @property
    def classes(self):
        return set((self.attrs.get("class") or "").split())

    def has_class(self, name):
        return name in self.classes

    @property
    def text(self):
        """Character data under this element: one space at every tag boundary, whitespace
        collapsed — `<p>one<p>two` reads "one two", like a rendered page would."""
        return _join(self._text)


class _Scope:
    """Queries restricted to one element's subtree (or the whole document)."""

    def __init__(self, els, sel_default, first, last, root=None):
        self._els = els
        self._sel_default = sel_default
        self._first, self._last = first, last
        self._root = root

    def _inside(self):
        return (el for el in self._els[self._first:self._last + 1])

    # --- queries ---
    def by_id(self, id_):
        for el in self._inside():
            if el["id"] == id_:
                return el
        return None

    def present(self, id_):
        return self.by_id(id_) is not None

    def find(self, tag, *, class_=None, **attrs):
        """Elements of `tag` whose attributes equal `attrs`; `class_` matches ONE class token
        (`class_="pill"` finds `class="pill mono"`), so a renamed neighbour class is harmless."""
        out = []
        for el in self._inside():
            if el.tag != tag:
                continue
            if class_ is not None and not el.has_class(class_):
                continue
            if all(el[k] == v for k, v in attrs.items()):
                out.append(el)
        return out

    def within(self, el):
        """The same queries, restricted to `el`'s subtree."""
        assert el is not None, "within(None): the element was not found"
        return _Scope(self._els, self._sel_default, el.index, el.end, root=el)

    def index(self, el):
        """Document order of a start tag — for `doc.index(a) < doc.index(b)` assertions."""
        return el.index

    @property
    def text(self):
        """The scope's character data, whitespace-collapsed (the whole page for the document)."""
        return self._root.text if self._root is not None else _join(self._doc_text)

    def field_default(self, name):
        """The rendered default of a form control IN THIS SCOPE: an <input>'s `value`, or a
        <select>'s selected <option> value (falling back to its first option)."""
        for el in self._inside():
            if el.tag == "select" and el["name"] == name:
                return self._sel_default.get(el.index)
            if el.tag == "input" and el["name"] == name:
                return el["value"]
        return None


class _Doc(HTMLParser, _Scope):
    def __init__(self):
        HTMLParser.__init__(self, convert_charrefs=True)
        self._els = []                    # every start tag, in document order
        self._open = []                   # stack of open elements
        self._sel = None                  # the <select> element being parsed
        self._sel_default = {}            # select element index -> selected option value (or first)
        self._doc_text = []               # every run of character data, for `doc.text`
        _Scope.__init__(self, self._els, self._sel_default, 0, -1)

    def handle_starttag(self, tag, attrs):
        el = _El(tag, attrs, len(self._els))
        self._els.append(el)
        self._last = len(self._els) - 1
        if tag == "select":
            self._sel = el
        elif tag == "option" and self._sel is not None:
            val = el["value"]
            if "selected" in el.attrs or self._sel.index not in self._sel_default:
                self._sel_default[self._sel.index] = val   # selected wins; else first option
        if tag not in _VOID:
            self._open.append(el)
        else:
            el.end = el.index

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag == "select":
            self._sel = None
        for i in range(len(self._open) - 1, -1, -1):
            if self._open[i].tag == tag:
                for el in self._open[i:]:              # unclosed children close with the parent
                    el.end = len(self._els) - 1
                del self._open[i:]
                return

    def handle_data(self, data):
        self._doc_text.append(data)
        for el in self._open:
            el._text.append(data)

    def close(self):
        HTMLParser.close(self)
        for el in self._open:                          # never closed: they run to the end
            el.end = len(self._els) - 1
        self._open = []


def parse(body):
    if isinstance(body, bytes):
        body = body.decode()
    doc = _Doc()
    doc.feed(body)
    doc.close()
    return doc
