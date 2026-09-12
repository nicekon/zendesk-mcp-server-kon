"""Non-rendering text extraction; output remains untrusted user content."""
from html.parser import HTMLParser


class HTMLText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []; self.has_images = False

    def handle_starttag(self, tag, attrs):
        if tag == "img":
            self.has_images = True
            alt = dict(attrs).get("alt")
            self.parts.append(f"[image: {alt}]" if alt else "[image]")
        elif tag in ("br", "p", "div", "li"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("p", "div", "li"): self.parts.append("\n")

    def handle_data(self, data): self.parts.append(data)

    def unknown_decl(self, data):
        raise ValueError("unsupported HTML declaration")
