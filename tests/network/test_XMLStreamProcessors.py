import io
from logging import exception
import pytest
import uuid

from aioxmpp import errors, structs, xml, xso
from aioxmppd.network import XMLStreamHandler, XMLStreamWriter


@pytest.fixture()
def processor():
    proc = XMLStreamHandler()
    yield proc
    del proc


@pytest.fixture()
def parser(processor):
    parser = xml.make_parser()
    parser.setContentHandler(processor)
    yield parser
    del parser


@pytest.fixture()
def buffer():
    buf = io.BytesIO()
    yield buf
    del buf


class Cls(xso.XSO):
    TAG = ("uri:foo", "bar")

    DECLARE_NS = {}


class Cls2(xso.XSO):
    TAG = ("uri:foo", "foo")

    text = xso.Text()


class TestXMLStreamHandler:
    TEST_VALID_HEADER = '<stream:stream xmlns:stream="http://etherx.jabber.org/streams" from="foo@example.test" to="example.test" version="1.0" xml:lang="en" xmlns="jabber:client">'

    TEST_STREAM_HEADER_TAG = ("http://etherx.jabber.org/streams", "stream")

    TEST_STREAM_HEADER_ATTRS = {
        (None, "to"): "example.test",
        (None, "from"): "foo@example.test",
        ("http://www.w3.org/XML/1998/namespace", "lang"): "en",
        (None, "version"): "1.0",
    }

    def test_reject_processing_instruction(self, processor):
        with pytest.raises(errors.StreamError):
            processor.processingInstruction("foo", "bar")

    def test_reject_start_element_without_ns(self, processor):
        with pytest.raises(RuntimeError):
            processor.startElement("foo", {})

    def test_reject_end_element_without_ns(self, processor):
        with pytest.raises(RuntimeError):
            processor.endElement("foo")

    def test_errors_propagate(self, processor):
        parser = xml.make_parser()
        parser.setContentHandler(processor)
        parser.feed(self.TEST_VALID_HEADER)
        with pytest.raises(errors.StreamError):
            parser.feed("<!-- foo -->")

    def test_capture_stream_header(self, processor):
        assert processor.remote_version == None
        assert processor.remote_from == None
        assert processor.remote_to == None
        assert processor.remote_lang == None

        processor.startDocument()
        processor.startElementNS(
            self.TEST_STREAM_HEADER_TAG, None, self.TEST_STREAM_HEADER_ATTRS
        )

        assert processor.remote_version == (1, 0)
        assert processor.remote_from == structs.JID.fromstr("foo@example.test")
        assert processor.remote_to == structs.JID.fromstr("example.test")
        assert processor.remote_lang == structs.LanguageTag.fromstr("en")

    def test_require_stream_header(self, processor):
        processor.startDocument()

        with pytest.raises(errors.StreamError):
            processor.startElementNS((None, "foo"), None, {})

        with pytest.raises(errors.StreamError):
            processor.startElementNS(
                ("http://etherx.jabber.org/streams", "bar"), None, {}
            )

    def test_do_not_require_stream_header_from(self, processor):
        attrs = self.TEST_STREAM_HEADER_ATTRS.copy()
        del attrs[(None, "from")]

        processor.startDocument()
        processor.startElementNS(self.TEST_STREAM_HEADER_TAG, None, attrs)

        assert None == processor.remote_from

    def test_require_stream_header_to(self, processor):
        attrs = self.TEST_STREAM_HEADER_ATTRS.copy()
        del attrs[(None, "to")]

        processor.startDocument()

        with pytest.raises(errors.StreamError):
            processor.startElementNS(self.TEST_STREAM_HEADER_TAG, None, attrs)

    def test_interpret_missing_version_as_0_point_9(self, processor):
        attrs = self.TEST_STREAM_HEADER_ATTRS.copy()
        del attrs[None, "version"]

        processor.startDocument()
        processor.startElementNS(self.TEST_STREAM_HEADER_TAG, None, attrs)

        assert processor.remote_version == (0, 9)

    def test_interpret_parsing_error_as_unsupported_version(self, processor):
        attrs = self.TEST_STREAM_HEADER_ATTRS.copy()
        attrs[None, "version"] = "foobar"

        processor.startDocument()
        with pytest.raises(errors.StreamError):
            processor.startElementNS(self.TEST_STREAM_HEADER_TAG, None, attrs)

    def test_forward_to_parser(self, processor):
        results = []

        def recv(obj):
            nonlocal results
            results.append(obj)

        processor.stanza_parser = xso.XSOParser()
        processor.stanza_parser.add_class(Cls, recv)

        processor.startDocument()
        processor.startElementNS(
            self.TEST_STREAM_HEADER_TAG, None, self.TEST_STREAM_HEADER_ATTRS
        )
        processor.startElementNS(Cls.TAG, None, {})
        processor.endElementNS(Cls.TAG, None)

        assert len(results) == 1
        assert isinstance(results[0], Cls)

    def test_require_start_document(self, processor):
        with pytest.raises(RuntimeError):
            processor.startElementNS((None, "foo"), None, {})
        with pytest.raises(RuntimeError):
            processor.endElementNS((None, "foo"), None)
        with pytest.raises(RuntimeError):
            processor.characters("foo")

    def test_parse_complex_class(self, processor):
        results = []

        def recv(obj):
            nonlocal results
            results.append(obj)

        class Bar(xso.XSO):
            TAG = ("uri:foo", "bar")

            text = xso.Text(default=None)

            def __init__(self, text=None):
                super().__init__()
                self.text = text

        class Baz(xso.XSO):
            TAG = ("uri:foo", "baz")

            children = xso.ChildList([Bar])

        class Foo(xso.XSO):
            TAG = ("uri:foo", "foo")

            attr = xso.Attr((None, "attr"))
            bar = xso.Child([Bar])
            baz = xso.Child([Baz])

        processor.stanza_parser = xso.XSOParser()
        processor.stanza_parser.add_class(Foo, recv)

        processor.startDocument()
        processor.startElementNS(
            self.TEST_STREAM_HEADER_TAG, None, self.TEST_STREAM_HEADER_ATTRS
        )

        f = Foo()
        f.attr = "fnord"
        f.bar = Bar()
        f.bar.text = "some text"
        f.baz = Baz()
        f.baz.children.append(Bar("child a"))
        f.baz.children.append(Bar("child b"))

        f.xso_serialise_to_sax(processor)

        assert len(results) == 1

        f2 = results.pop()
        assert f.attr == f2.attr
        assert f.bar.text == f2.bar.text
        assert len(f.baz.children) == len(f2.baz.children)
        for c1, c2 in zip(f.baz.children, f2.baz.children):
            assert c1.text == c2.text

        processor.endElementNS(self.TEST_STREAM_HEADER_TAG, None)
        processor.endDocument()

    def test_require_end_document_before_restarting(self, processor):
        processor.startDocument()
        processor.startElementNS(
            self.TEST_STREAM_HEADER_TAG, None, self.TEST_STREAM_HEADER_ATTRS
        )

        with pytest.raises(RuntimeError):
            processor.startDocument()
        processor.endElementNS(self.TEST_STREAM_HEADER_TAG, None)

        with pytest.raises(RuntimeError):
            processor.startDocument()
        processor.endDocument()
        processor.startDocument()

    def test_allow_end_document_only_after_stream_has_finished(self, processor):
        with pytest.raises(RuntimeError):
            processor.endDocument()
        processor.startDocument()

        with pytest.raises(RuntimeError):
            processor.endDocument()
        processor.startElementNS(
            self.TEST_STREAM_HEADER_TAG, None, self.TEST_STREAM_HEADER_ATTRS
        )

        with pytest.raises(RuntimeError):
            processor.endDocument()
        processor.endElementNS(self.TEST_STREAM_HEADER_TAG, None)
        processor.endDocument()

    def test_disallow_changing_stanza_parser_during_processing(self, processor):
        processor.stanza_parser = xso.XSOParser()
        processor.startDocument()

        with pytest.raises(RuntimeError):
            processor.stanza_parser = xso.XSOParser()

    def test_on_stream_header(self, processor):
        stream_header = ()

        def catch_stream_header():
            nonlocal stream_header
            stream_header = (
                processor.remote_from,
                processor.remote_to,
                processor.remote_version,
                processor.remote_lang,
            )

        assert processor.on_stream_header == None
        processor.on_stream_header = catch_stream_header
        processor.startDocument()
        processor.startElementNS(
            self.TEST_STREAM_HEADER_TAG, None, self.TEST_STREAM_HEADER_ATTRS
        )
        assert stream_header == (
            processor.remote_from,
            processor.remote_to,
            processor.remote_version,
            processor.remote_lang,
        )

    def test_on_stream_footer(self, processor):
        stream_footer = False

        def catch_stream_footer():
            nonlocal stream_footer
            stream_footer = True

        assert processor.on_stream_footer == None
        processor.on_stream_footer = catch_stream_footer
        processor.startDocument()
        processor.startElementNS(
            self.TEST_STREAM_HEADER_TAG, None, self.TEST_STREAM_HEADER_ATTRS
        )
        processor.endElementNS(self.TEST_STREAM_HEADER_TAG, None)
        processor.endDocument()

        assert stream_footer

    def test_exception_recovery_and_reporting(self, processor):
        stream_exception = None

        def catch_exception(exc):
            nonlocal stream_exception
            stream_exception = exc

        elements = []

        def recv(obj):
            nonlocal elements
            elements.append(obj)

        class Child(xso.XSO):
            TAG = ("uri:foo", "bar")

        class Foo(xso.XSO):
            TAG = ("uri:foo", "foo")

        assert processor.on_exception == None
        processor.on_exception = catch_exception
        processor.stanza_parser = xso.XSOParser()
        processor.stanza_parser.add_class(Foo, recv)
        processor.startDocument()
        processor.startElementNS(
            self.TEST_STREAM_HEADER_TAG, None, self.TEST_STREAM_HEADER_ATTRS
        )
        processor.startElementNS((None, "foo"), None, {})
        processor.startElementNS((None, "bar"), None, {})
        processor.characters("foobar")
        processor.endElementNS((None, "bar"), None)
        processor.endElementNS((None, "foo"), None)

        assert stream_exception

        processor.startElementNS(("uri:foo", "foo"), None, {})
        processor.endElementNS(("uri:foo", "foo"), None)

        assert elements
        assert isinstance(elements[0], Foo)

        processor.endElementNS(self.TEST_STREAM_HEADER_TAG, None)
        processor.endDocument()

    def test_exception_in_endElementNS_recovery_and_reporting(self, processor):
        stream_exception = None

        def catch_exception(exc):
            nonlocal stream_exception
            stream_exception = exc

        elements = []

        def recv(obj):
            nonlocal elements
            elements.append(obj)

        class Child(xso.XSO):
            TAG = ("uri:foo", "bar")

            t = xso.Text(type_=xso.Float())

        class Foo(xso.XSO):
            TAG = ("uri:foo", "foo")

            c = xso.Child([Child])

        assert processor.on_exception == None
        processor.on_exception = catch_exception
        processor.stanza_parser = xso.XSOParser()
        processor.stanza_parser.add_class(Foo, recv)
        processor.startDocument()
        processor.startElementNS(
            self.TEST_STREAM_HEADER_TAG, None, self.TEST_STREAM_HEADER_ATTRS
        )
        processor.startElementNS(("uri:foo", "foo"), None, {})
        processor.startElementNS(("uri:foo", "bar"), None, {})
        processor.characters("foobar")
        processor.endElementNS(("uri:foo", "bar"), None)
        processor.endElementNS(("uri:foo", "foo"), None)

        assert stream_exception

        processor.startElementNS(("uri:foo", "foo"), None, {})
        processor.endElementNS(("uri:foo", "foo"), None)

        assert elements
        assert isinstance(elements[0], Foo)

        processor.endElementNS(self.TEST_STREAM_HEADER_TAG, None)
        processor.endDocument()

    def test_exception_in_endElementNS_toplevel_recovery_and_reporting(self, processor):
        stream_exception = None

        def catch_exception(exc):
            nonlocal stream_exception
            stream_exception = exc

        elements = []

        def recv(obj):
            nonlocal elements
            elements.append(obj)

        class Foo(xso.XSO):
            TAG = ("uri:foo", "foo")

            t = xso.Text(type_=xso.Float())

        assert processor.on_exception == None
        processor.on_exception = catch_exception
        processor.stanza_parser = xso.XSOParser()
        processor.stanza_parser.add_class(Foo, recv)
        processor.startDocument()
        processor.startElementNS(
            self.TEST_STREAM_HEADER_TAG, None, self.TEST_STREAM_HEADER_ATTRS
        )
        processor.startElementNS(("uri:foo", "foo"), None, {})
        processor.characters("foobar")
        processor.endElementNS(("uri:foo", "foo"), None)

        assert stream_exception

        processor.startElementNS(("uri:foo", "foo"), None, {})
        processor.endElementNS(("uri:foo", "foo"), None)

        assert elements
        assert isinstance(elements[0], Foo)

        processor.endElementNS(self.TEST_STREAM_HEADER_TAG, None)
        processor.endDocument()

    def test_exception_reraise_without_handler(self, processor):
        elements = []

        def recv(obj):
            nonlocal elements
            elements.append(obj)

        class Child(xso.XSO):
            TAG = ("uri:foo", "bar")

        class Foo(xso.XSO):
            TAG = ("uri:foo", "foo")

        processor.stanza_parser = xso.XSOParser()
        processor.stanza_parser.add_class(Foo, recv)
        processor.startDocument()
        processor.startElementNS(
            self.TEST_STREAM_HEADER_TAG, None, self.TEST_STREAM_HEADER_ATTRS
        )
        processor.startElementNS((None, "foo"), None, {})
        processor.startElementNS((None, "bar"), None, {})
        processor.characters("foobar")
        processor.endElementNS((None, "bar"), None)

        with pytest.raises(ValueError):
            processor.endElementNS((None, "foo"), None)

    def test_forwards_xml_lang_to_parser(self, processor):
        results = []

        def recv(obj):
            nonlocal results
            results.append(obj)

        class Foo(xso.XSO):
            TAG = ("uri:foo", "foo")

            attr = xso.LangAttr()

        processor.stanza_parser = xso.XSOParser()
        processor.stanza_parser.add_class(Foo, recv)

        attrs = dict(self.TEST_STREAM_HEADER_ATTRS)
        attrs["http://www.w3.org/XML/1998/namespace", "lang"] = "en"

        processor.startDocument()
        processor.startElementNS(self.TEST_STREAM_HEADER_TAG, None, attrs)

        processor.startElementNS(Foo.TAG, None, {})
        processor.endElementNS(Foo.TAG, None)

        assert len(results) == 1

        f = results.pop()
        assert f.attr == structs.LanguageTag.fromstr("en")

        processor.endElementNS(self.TEST_STREAM_HEADER_TAG, None)
        processor.endDocument()


class TestXMLStreamWriter:
    TEST_ID = str(uuid.uuid4())
    TEST_TO = "foo@example.test"
    TEST_FROM = "example.test"
    TEST_XML = '<?xml version="1.0"?>'
    TEST_STREAM_HEADER = "<stream:stream "
    TEST_STREAM_NS = 'xmlns:stream="http://etherx.jabber.org/streams"'
    TEST_STREAM_FOOTER = "</stream:stream>"

    def test_no_write_before_start(self, buffer):
        writer = XMLStreamWriter(
            buffer, self.TEST_ID, structs.JID.fromstr(self.TEST_FROM)
        )

        assert b"" == buffer.getvalue()

    def test_setup(self, buffer):
        writer = XMLStreamWriter(
            buffer, self.TEST_ID, structs.JID.fromstr(self.TEST_FROM)
        )
        writer.start()
        writer.close()

        assert (
            self.TEST_XML
            + self.TEST_STREAM_HEADER
            + self.TEST_STREAM_NS
            + ' id="'
            + self.TEST_ID
            + '" from="'
            + self.TEST_FROM
            + '" version="1.0">'
            + self.TEST_STREAM_FOOTER
        ).encode() == buffer.getvalue()

    def test_to(self, buffer):
        writer = XMLStreamWriter(
            buffer,
            self.TEST_ID,
            structs.JID.fromstr(self.TEST_FROM),
            to=structs.JID.fromstr(self.TEST_TO),
        )
        writer.start()
        writer.close()

        assert (
            self.TEST_XML
            + self.TEST_STREAM_HEADER
            + self.TEST_STREAM_NS
            + ' id="'
            + self.TEST_ID
            + '" from="'
            + self.TEST_FROM
            + '" version="1.0" to="'
            + self.TEST_TO
            + '">'
            + self.TEST_STREAM_FOOTER
        ).encode() == buffer.getvalue()

    def test_reset(self, buffer):
        writer = XMLStreamWriter(
            buffer, self.TEST_ID, structs.JID.fromstr(self.TEST_FROM)
        )
        writer.start()
        writer.abort()

        assert (
            self.TEST_XML
            + self.TEST_STREAM_HEADER
            + self.TEST_STREAM_NS
            + ' id="'
            + self.TEST_ID
            + '" from="'
            + self.TEST_FROM
            + '" version="1.0">'
        ).encode() == buffer.getvalue()

    def test_root_ns(self, buffer):
        writer = XMLStreamWriter(
            buffer,
            self.TEST_ID,
            structs.JID.fromstr(self.TEST_FROM),
            nsmap={None: "jabber:client"},
        )
        writer.start()
        writer.abort()

        assert (
            self.TEST_XML
            + self.TEST_STREAM_HEADER
            + 'xmlns="jabber:client" '
            + self.TEST_STREAM_NS
            + ' id="'
            + self.TEST_ID
            + '" from="'
            + self.TEST_FROM
            + '" version="1.0">'
        ).encode() == buffer.getvalue()

    def test_send_object(self, buffer):
        obj = Cls()
        writer = XMLStreamWriter(
            buffer, self.TEST_ID, structs.JID.fromstr(self.TEST_FROM)
        )
        writer.start()
        writer.send(obj)
        writer.close()

        assert (
            self.TEST_XML
            + self.TEST_STREAM_HEADER
            + self.TEST_STREAM_NS
            + ' id="'
            + self.TEST_ID
            + '" from="'
            + self.TEST_FROM
            + '" version="1.0">'
            + '<bar xmlns="uri:foo"/>'
            + self.TEST_STREAM_FOOTER
        ).encode() == buffer.getvalue()

    def test_send_object_inherits_namespaces(self, buffer):
        obj = Cls()
        writer = XMLStreamWriter(
            buffer,
            self.TEST_ID,
            structs.JID.fromstr(self.TEST_FROM),
            nsmap={"jc": "uri:foo"},
        )
        writer.start()
        writer.send(obj)
        writer.close()

        assert (
            self.TEST_XML
            + self.TEST_STREAM_HEADER
            + 'xmlns:jc="uri:foo" '
            + self.TEST_STREAM_NS
            + ' id="'
            + self.TEST_ID
            + '" from="'
            + self.TEST_FROM
            + '" version="1.0">'
            + "<jc:bar/>"
            + self.TEST_STREAM_FOOTER
        ).encode() == buffer.getvalue()

    def test_send_handles_serialisation_issues_gracefully(self, buffer):
        obj = Cls2()
        obj.text = "foo\0"

        writer = XMLStreamWriter(
            buffer,
            self.TEST_ID,
            structs.JID.fromstr(self.TEST_FROM),
            nsmap={"jc": "uri:foo"},
        )
        writer.start()
        with pytest.raises(ValueError):
            writer.send(obj)
        writer.close()

    def test_close_is_idempotent(self, buffer):
        obj = Cls()
        writer = XMLStreamWriter(
            buffer, self.TEST_ID, structs.JID.fromstr(self.TEST_FROM)
        )
        writer.start()
        writer.send(obj)
        writer.close()
        writer.close()
        writer.close()

        assert (
            self.TEST_XML
            + self.TEST_STREAM_HEADER
            + self.TEST_STREAM_NS
            + ' id="'
            + self.TEST_ID
            + '" from="'
            + self.TEST_FROM
            + '" version="1.0">'
            + '<bar xmlns="uri:foo"/>'
            + self.TEST_STREAM_FOOTER
        ).encode() == buffer.getvalue()

    def test_abort_makes_close_noop(self, buffer):
        obj = Cls()
        writer = XMLStreamWriter(
            buffer, self.TEST_ID, structs.JID.fromstr(self.TEST_FROM)
        )
        writer.start()
        writer.send(obj)
        writer.abort()
        writer.close()

        assert (
            self.TEST_XML
            + self.TEST_STREAM_HEADER
            + self.TEST_STREAM_NS
            + ' id="'
            + self.TEST_ID
            + '" from="'
            + self.TEST_FROM
            + '" version="1.0">'
            + '<bar xmlns="uri:foo"/>'
        ).encode() == buffer.getvalue()

    def test_abort_is_idempotent(self, buffer):
        obj = Cls()
        writer = XMLStreamWriter(
            buffer, self.TEST_ID, structs.JID.fromstr(self.TEST_FROM)
        )
        writer.start()
        writer.send(obj)
        writer.abort()
        writer.abort()
        writer.abort()

        assert (
            self.TEST_XML
            + self.TEST_STREAM_HEADER
            + self.TEST_STREAM_NS
            + ' id="'
            + self.TEST_ID
            + '" from="'
            + self.TEST_FROM
            + '" version="1.0">'
            + '<bar xmlns="uri:foo"/>'
        ).encode() == buffer.getvalue()

    def test_abort_after_close_is_okay(self, buffer):
        obj = Cls()
        writer = XMLStreamWriter(
            buffer, self.TEST_ID, structs.JID.fromstr(self.TEST_FROM)
        )
        writer.start()
        writer.send(obj)
        writer.close()
        writer.abort()

        assert (
            self.TEST_XML
            + self.TEST_STREAM_HEADER
            + self.TEST_STREAM_NS
            + ' id="'
            + self.TEST_ID
            + '" from="'
            + self.TEST_FROM
            + '" version="1.0">'
            + '<bar xmlns="uri:foo"/>'
            + self.TEST_STREAM_FOOTER
        ).encode() == buffer.getvalue()
