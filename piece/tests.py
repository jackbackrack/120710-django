from django.test import TestCase

from .models import Artist


class ArtistModelTests(TestCase):
	def test_save_splits_legacy_name(self):
		artist = Artist.objects.create(
			name="Ada Lovelace",
			email="ada@example.com",
			phone="555-1212",
		)

		self.assertEqual(artist.first_name, "Ada")
		self.assertEqual(artist.last_name, "Lovelace")
		self.assertEqual(artist.full_name, "Ada Lovelace")

	def test_save_syncs_legacy_name_from_split_fields(self):
		artist = Artist.objects.create(
			first_name="Grace",
			last_name="Hopper",
			email="grace@example.com",
			phone="555-3434",
			name="",
		)

		self.assertEqual(artist.name, "Grace Hopper")
		self.assertEqual(str(artist), "Grace Hopper")
