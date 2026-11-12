import pytest

from fastcs.util import optional_dependency, snake_to_pascal


def test_snake_to_pascal():
    name1 = "name_in_snake_case"
    name2 = "name-not-in-snake-case"
    name3 = "name_with-different_separators"
    name4 = "name_with_numbers_1_2_3"
    name5 = "numbers_1_2_3_in_the_middle"
    name6 = "1_2_3_starting_with_numbers"
    name7 = "name1_with2_a3_number4"
    name8 = "name_in_lower_case"
    name9 = "NameAlreadyInPascalCase"
    name10 = "Name_With_%_Invalid_&_Symbols_£_"
    name11 = "a_b_c_d"
    name12 = "test"
    assert snake_to_pascal(name1) == "NameInSnakeCase"
    assert snake_to_pascal(name2) == "name-not-in-snake-case"
    assert snake_to_pascal(name3) == "name_with-different_separators"
    assert snake_to_pascal(name4) == "NameWithNumbers123"
    assert snake_to_pascal(name5) == "Numbers123InTheMiddle"
    assert snake_to_pascal(name6) == "1_2_3_starting_with_numbers"
    assert snake_to_pascal(name7) == "Name1With2A3Number4"
    assert snake_to_pascal(name8) == "NameInLowerCase"
    assert snake_to_pascal(name9) == "NameAlreadyInPascalCase"
    assert snake_to_pascal(name10) == "Name_With_%_Invalid_&_Symbols_£_"
    assert snake_to_pascal(name11) == "ABCD"
    assert snake_to_pascal(name12) == "Test"


def test_optional_dependency_skips_listed_missing_package():
    with optional_dependency("missing_package"):
        import missing_package.submodule  # noqa: F401


def test_optional_dependency_raises_for_other_missing_module():
    with pytest.raises(ModuleNotFoundError, match="other_missing_package"):
        with optional_dependency("missing_package"):
            import other_missing_package  # noqa: F401
