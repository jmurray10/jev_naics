"""The NAICS search data (JEV-50): every 2022 industry with its full official text, generated from
the Census files by build_search.py and shipped as package data."""

from jev_naics.search_data import SEARCH_DATA_PATH, load_search_data
from jev_naics.tree import load_tree


def test_every_industry_is_there_with_clean_titles() -> None:
    assert SEARCH_DATA_PATH.is_file(), "build it: python -m jev_naics.build_search"
    data = load_search_data()
    assert len(data.industries) == 1012
    assert set(data.industries) == set(load_tree().leaves())
    # 799 Census titles carry a trailing "T" (trilateral) marker; none survives.
    assert data.industries["541940"].title == "Veterinary Services"
    assert not any(i.title.endswith("ServicesT") for i in data.industries.values())


def test_the_description_examples_and_index_entries_are_separate_and_official() -> None:
    vet = load_search_data().industries["541940"]
    assert vet.description.startswith("This industry comprises establishments of licensed veterinary")
    assert "Illustrative Examples" not in vet.description and "Cross-References" not in vet.description
    assert vet.examples == ("Animal hospitals", "Veterinary clinics", "Veterinarians' offices",
                            "Veterinary testing laboratories")
    assert "Pet hospitals" in vet.index_entries
    assert sum(len(i.index_entries) for i in load_search_data().industries.values()) == 20373


def test_a_cross_reference_excludes_here_and_sends_the_activity_to_its_industry() -> None:
    data = load_search_data()
    vet, pets = data.industries["541940"], data.industries["812910"]
    # Under 541940 the Census says pet grooming is classified elsewhere: an exclusion here ...
    assert ("812910", "Providing nonveterinary pet care services, such as boarding or grooming pets") in vet.excludes
    # ... and an activity that belongs to 812910, where search should find it.
    assert "Providing nonveterinary pet care services, such as boarding or grooming pets" in pets.sent_here
    assert not any("grooming" in text for text in vet.sent_here)


def test_the_data_records_its_source_and_a_version_for_reloading() -> None:
    data = load_search_data()
    assert "Census" in data.source and len(data.version) == 16
