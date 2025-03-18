from fuzzywuzzy import fuzz

from app.models.category import Category
from app.synonyms import get_synonyms

synonyms = get_synonyms()


def normalize(text: str):
    return text.strip().lower()


def get_synonym(category):
    # Check if the normalized category exists in synonyms, otherwise return original
    return synonyms.get(normalize(category), category)


def find_cat_match(name: str, categories: list[Category], threshold=80):
    name_normalized = get_synonym(name)
    best_match = None
    best_score = 0

    for category in categories:
        category_score = fuzz.ratio(category["name"], name_normalized)
        if category_score > best_score and category_score >= threshold:
            best_match = {"id": category["id"], "name": category["name"]}
            best_score = category_score

    return best_match


def find_subcat_match(
    name: str, category_id: int, categories: list[Category], threshold=80
):
    name_normalized = get_synonym(name)
    best_match = None
    best_score = 0

    for category in categories:
        if category["id"] != category_id:
            continue
        for subcategory in category["subcategories"]:
            subcategory_score = fuzz.ratio(subcategory["name"], name_normalized)
            if subcategory_score > best_score and subcategory_score >= threshold:
                best_match = {"id": subcategory["id"], "name": subcategory["name"]}
                best_score = subcategory_score
        break

    return best_match
