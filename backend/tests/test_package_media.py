"""Tour package media helpers must send usable image/PDF URLs only."""

import os
import sys
import unittest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(BACKEND_DIR)
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from services.package_flow import (  # noqa: E402
    package_media,
    package_name,
    _usable_media_url,
)


class PackageMediaTests(unittest.TestCase):
    def test_rejects_placeholder_urls(self):
        self.assertFalse(_usable_media_url("Add public https image URL"))
        self.assertFalse(_usable_media_url(""))
        self.assertFalse(_usable_media_url("n/a"))

    def test_accepts_hosted_urls(self):
        self.assertTrue(_usable_media_url("https://i.imghippo.com/files/x.jpg"))
        self.assertTrue(_usable_media_url("/uploaded_files/product_images/a.jpg"))

    def test_package_media_prefers_image(self):
        pkg = {
            "name": "Kashmir Paradise",
            "media_url": "https://cdn.example.com/kashmir.jpg",
            "media_type": "image",
            "attrs_json": {"package name": "Kashmir Paradise", "destination": "Srinagar"},
        }
        url, kind = package_media(pkg)
        self.assertEqual(url, "https://cdn.example.com/kashmir.jpg")
        self.assertEqual(kind, "image")

    def test_package_media_pdf_from_attrs(self):
        # A brochure is sent only when the user asks for one. A placeholder
        # image must not promote that PDF onto the package card.
        pkg = {
            "name": "Goa Getaway",
            "media_url": "Add public https image URL",
            "attrs_json": {
                "package_name": "Goa Getaway",
                "brochure_url": "/uploaded_files/brochures/goa.pdf",
            },
        }
        url, kind = package_media(pkg)
        self.assertEqual(url, "")
        self.assertEqual(kind, "")

        hosted = {
            "name": "Goa Getaway",
            "media_url": "/uploaded_files/brochures/goa.pdf",
            "media_type": "document",
        }
        url, kind = package_media(hosted)
        self.assertTrue(url.endswith("goa.pdf"))
        self.assertEqual(kind, "document")

    def test_package_name_reads_spaced_key(self):
        pkg = {
            "name": "Fallback",
            "attrs_json": {"package name": "Dubai Delight"},
        }
        self.assertEqual(package_name(pkg), "Dubai Delight")


if __name__ == "__main__":
    unittest.main()
