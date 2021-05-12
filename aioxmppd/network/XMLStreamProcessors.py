import xml.sax

from aioxmpp import errors, structs, xso
from aioxmpp.xml import XMLStreamWriter
from enum import Enum
from loguru import logger


class HandlerState(Enum):
    """
    Possible states of the XMLStreamHandler class
    """

    CLEAN = 0
    STARTED = 1
    STREAM_HEADER_PROCESSED = 2
    STREAM_FOOTER_PROCESSED = 3
    EXCEPTION = 4


class XMLStreamHandler(xml.sax.ContentHandler):
    """
    Useful class to parse a incoming XML stream. Inherits from xml.sax.ContentHandler

    The following callabcks can be defined for the next events:
    * on_stream_header: Called after a stream header is processed by the parser.
    * on_stream_footer: Called after a stream footer is processed by the parser.
    * on_expcetion: Called after processing a stream when a exception occurs.
    """

    def __init__(self):
        self._state = HandlerState.CLEAN
        self._stanza_parser = xso.XSOParser()
        self._depth = None
        self._stored_exception = None

        # Callbacks
        self.on_stream_header = None
        self.on_stream_footer = None
        self.on_exception = None

        # Client data
        self.remote_version = None
        self.remote_from = None
        self.remote_to = None
        self.remote_lang = None

    @property
    def stanza_parser(self):
        """
        A :class:`~.xso.XSOParser` object (or compatible) used to parse
        the xml stanzas. This object can only be set before :meth:`startDocument` has been
        called (or after :meth:`endDocument` has been called).
        """
        return self._stanza_parser

    @stanza_parser.setter
    def stanza_parser(self, value):
        if self._state != HandlerState.CLEAN:
            raise RuntimeError("invalid state: {}".format(self._state))
        self._stanza_parser = value
        self._stanza_parser.lang = self.remote_lang

    def processingInstruction(self, target, foo):
        raise errors.StreamError(
            errors.StreamErrorCondition.RESTRICTED_XML,
            "processing instructions are not allowed in XMPP",
        )

    def characters(self, characters):
        if self._state == HandlerState.EXCEPTION:
            pass
        elif self._state != HandlerState.STREAM_HEADER_PROCESSED:
            raise RuntimeError(f"Invalid state: {self._state}")
        else:
            self._driver.characters(characters)

    def startDocument(self):
        if self._state != HandlerState.CLEAN:
            raise RuntimeError(f"Invalid state: {self._state}")

        self._state = HandlerState.STARTED
        self._depth = 0
        self._driver = xso.SAXDriver(self._stanza_parser)

    def endDocument(self):
        if self._state != HandlerState.STREAM_FOOTER_PROCESSED:
            raise RuntimeError(f"Invalid state: {self._state}")
        self._state = HandlerState.CLEAN
        self._driver = None

    def startElement(self, name, attributes):
        raise RuntimeError(
            "incorrectly configured parser: "
            "startElement called (instead of startElementNS)"
        )

    def endElement(self, name):
        raise RuntimeError(
            "incorrectly configured parser: "
            "endElement called (instead of endElementNS)"
        )

    def startPrefixMapping(self, prefix, uri):
        pass

    def endPrefixMapping(self, prefix):
        pass

    def startElementNS(self, name, qname, attributes):
        if self._state == HandlerState.STREAM_HEADER_PROCESSED:
            try:
                self._driver.startElementNS(name, qname, attributes)
            except Exception as exc:
                self._stored_exception = exc
                self._state = HandlerState.EXCEPTION
            self._depth += 1
            return
        elif self._state == HandlerState.EXCEPTION:
            self._depth += 1
            return
        elif self._state != HandlerState.STARTED:
            raise RuntimeError(f"Invalid state: {self._state}")

        if name != ("http://etherx.jabber.org/streams", "stream"):
            raise errors.StreamError(
                errors.StreamErrorCondition.INVALID_NAMESPACE,
                "Stream has invalid namespace or localname",
            )

        attributes = dict(attributes)

        # from (only on secure stream, not mandatory)
        remote_from = attributes.pop((None, "from"), None)
        if remote_from is not None:
            remote_from = structs.JID.fromstr(remote_from)
        self.remote_from = remote_from

        # to
        try:
            self.remote_to = structs.JID.fromstr(attributes.pop((None, "to")))
        except KeyError:
            raise errors.StreamError(
                errors.StreamErrorCondition.UNDEFINED_CONDITION,
                "Required to attribute in stream header",
            )

        # Protocol version
        try:
            self.remote_version = tuple(
                map(int, attributes.pop((None, "version"), "0.9").split("."))
            )
        except ValueError as exc:
            raise errors.StreamError(
                errors.StreamErrorCondition.UNSUPPORTED_VERSION, str(exc)
            )

        # xml lang
        try:
            lang = attributes.pop(("http://www.w3.org/XML/1998/namespace", "lang"))
        except KeyError:
            self.remote_lang = None
        else:
            self.remote_lang = structs.LanguageTag.fromstr(lang)

        if self._stanza_parser is not None:
            self._stanza_parser.lang = self.remote_lang

        if self.on_stream_header:
            self.on_stream_header()

        self._state = HandlerState.STREAM_HEADER_PROCESSED
        self._depth += 1

    def endElementNS(self, name, qname):
        if self._state == HandlerState.STREAM_HEADER_PROCESSED:
            self._depth -= 1
            if self._depth > 0:
                try:
                    return self._driver.endElementNS(name, qname)
                except Exception as exc:
                    self._stored_exception = exc
                    self._state = HandlerState.EXCEPTION
                    if self._depth == 1:
                        self._raise_exception()
            else:
                if self.on_stream_footer:
                    self.on_stream_footer()
                self._state = HandlerState.STREAM_FOOTER_PROCESSED

        elif self._state == HandlerState.EXCEPTION:
            self._depth -= 1
            if self._depth == 1:
                self._raise_exception()
        else:
            raise RuntimeError(f"Invalid state: {self._state}")

    def _raise_exception(self):
        self._state = HandlerState.STREAM_HEADER_PROCESSED
        exc = self._stored_exception
        self._stored_exception = None
        if self.on_exception:
            self.on_exception(exc)
        else:
            raise exc


class XMLStreamWriter(XMLStreamWriter):
    """Class to write conforming stream xml. Inherits from aioxmpp XMLStreamWriter class.

    :param f: buffer to write the data (usually the transport layer)
    :param id_: Unique identifier for the stream connection.
    :type id_: str
    :param from_: Address of the xmpp server (mandatory)
    :type from_: :class:`aioxmpp.JID`
    :param to: Optional address from which the connection originates.
    :type to: :class:`aioxmpp.JID`
    :param version: Version of the XML stream protocol.
    :type version: :class:`tuple` of (:class:`int`, :class:`int`)
    :param nsmap: Mapping of namespaces to declare at the stream header.
    :param sorted_attributes: Sort the attributes in the output. Note: this
        comes with a performance penalty.
    :type sorted_attributes: :class:`bool`

    """

    def __init__(
        self,
        f,
        id_,
        from_,
        to=None,
        version=(1, 0),
        nsmap={},
        sorted_attributes=False,
    ):
        super().__init__(f, to, from_, version, nsmap, sorted_attributes)

        self._id = id_

    def start(self):
        """
        Sends a stream header response to incomming connection from a client
        """
        attrs = {
            (None, "id"): str(self._id),
            (None, "from"): str(self._from),
            (None, "version"): ".".join(map(str, self._version)),
        }
        if self._to:
            attrs[None, "to"] = str(self._to)

        self._writer.startDocument()
        for prefix, uri in self._nsmap_to_use.items():
            self._writer.startPrefixMapping(prefix, uri)
        self._writer.startElementNS(
            ("http://etherx.jabber.org/streams", "stream"), None, attrs
        )
        self._writer.flush()