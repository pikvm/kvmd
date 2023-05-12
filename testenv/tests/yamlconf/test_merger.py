
import pytest

from kvmd.yamlconf import merger


@pytest.mark.asyncio
async def test_default_merge_strategy() -> None:
    dest: dict = {"a": "1", "b": "2", "c": [1, 2, 3, {"d": 4}]}
    src: dict = {"a": "3", "b": "2", "c": [3, 4, 5, {"e": 6}]}
    merger.yaml_merge(dest, src)
    assert dest == {"a": "3", "b": "2", "c": [3, 4, 5, {"e": 6}]}


@pytest.mark.asyncio
async def test_merge_strategy_merge() -> None:
    dest: dict = {"a": "1", "b": "2", "c": [1, 2, 3, {"d": 4}]}
    src: dict = {"Merge Strategy": "Merge", "a": "3", "b": "2", "c": [3, 4, 5, {"e": 6}]}
    merger.yaml_merge(dest, src)
    assert dest == {"a": "3", "b": "2", "c": [3, 4, 5, {"e": 6}]}


@pytest.mark.asyncio
async def test_default_is_same_as_merge_strategy() -> None:
    exp_result: dict = {"a": "3", "b": "2", "c": [3, 4, 5, {"e": 6}]}
    dest: dict = {"a": "1", "b": "2", "c": [1, 2, 3, {"d": 4}]}
    src: dict = {"a": "3", "b": "2", "c": [3, 4, 5, {"e": 6}]}
    merger.yaml_merge(dest, src)
    assert dest == exp_result
    src: dict = {"Merge Strategy": "Merge", "a": "3", "b": "2"}
    merger.yaml_merge(dest, src)
    assert dest == exp_result


@pytest.mark.asyncio
async def test_merge_strategy_disabled() -> None:
    dest: dict = {"a": "1", "b": "2", "c": [1, 2, 3, {"d": 4}]}
    src: dict = {"Merge Strategy": "disabled", "a": "3", "b": "2", "c": [3, 4, 5, {"e": 6}]}
    merger.yaml_merge(dest, src)
    assert dest == {"a": "1", "b": "2", "c": [1, 2, 3, {"d": 4}]}  # dest remains the same as the merge is disabled


@pytest.mark.asyncio
async def test_merge_strategy_deep_merge() -> None:
    dest: dict = {"a": "1", "b": "2", "c": [1, 2, 3, {"d": 4}]}
    src: dict = {"Merge Strategy": "Deep Merge", "a": "3", "b": "2", "c": [3, 4, 5, {"e": 6}]}
    merger.yaml_merge(dest, src)
    assert dest == {"a": "3", "b": "2", "c": [1, 2, 3, {"d": 4}, 4, 5, {"e": 6}]}  # lists are appended and dictionaries are merged


@pytest.mark.asyncio
async def test_merge_strategy_append() -> None:
    dest: dict = {"a": "1", "b": "2", "c": [1, 2, 3, {"d": 4}]}
    src: dict = {"Merge Strategy": "append", "a": "3", "b": "2", "c": [5, 6, 7, {"e": 8}]}
    merger.yaml_merge(dest, src)
    assert dest == {"a": "1", "b": "2", "c": [1, 2, 3, {"d": 4}, 5, 6, 7, {"e": 8}]}  # new values are appended to lists, existing dictionary keys are updated


@pytest.mark.asyncio
async def test_empty_dest() -> None:
    dest: dict = {}
    src: dict = {"Merge Strategy": "merge", "a": "3", "b": "2"}
    merger.yaml_merge(dest, src)
    assert dest == {"a": "3", "b": "2"}  # dest is updated with src


@pytest.mark.asyncio
async def test_empty_src() -> None:
    dest: dict = {"a": "1", "b": "2"}
    src: dict = {}
    merger.yaml_merge(dest, src)
    assert dest == {"a": "1", "b": "2"}  # dest remains the same as src is empty


@pytest.mark.asyncio
async def test_none_dest() -> None:
    dest: dict = None
    src: dict = {"a": "1", "b": "2"}
    merger.yaml_merge(dest, src)
    assert dest is None  # dest remains the same as src is empty


@pytest.mark.asyncio
async def test_none_src() -> None:
    dest: dict = {"a": "1", "b": "2"}
    src: dict = None
    merger.yaml_merge(dest, src)
    assert dest == {"a": "1", "b": "2"}  # dest remains the same as src is empty


@pytest.mark.asyncio
async def test_nested_dict() -> None:
    dest: dict = {"a": "1", "b": "2", "c": {"d": "3"}}
    src: dict = {"Merge Strategy": "merge", "a": "4", "c": {"e": "5"}}
    merger.yaml_merge(dest, src)
    assert dest == {"a": "4", "b": "2", "c": {"d": "3", "e": "5"}}  # nested dictionary is merged


@pytest.mark.asyncio
async def test_list_in_dict() -> None:
    dest: dict = {"a": "1", "b": ["2", "3"]}
    src: dict = {"Merge Strategy": "deep_merge", "b": ["4", "5"]}
    merger.yaml_merge(dest, src)
    assert dest == {"a": "1", "b": ["2", "3", "4", "5"]}  # list inside dictionary is appended with deep_merge


@pytest.mark.asyncio
async def test_merge_strategy_merge_list_in_list() -> None:
    dest: dict = {"a": [[1, 2], [3, 4]]}
    src: dict = {"Merge Strategy": "merge", "a": [[5, 6]]}
    merger.yaml_merge(dest, src)
    assert dest == {"a": [[5, 6]]}  # list is replaced, not appended


@pytest.mark.asyncio
async def test_merge_strategy_merge_nested_dict() -> None:
    dest: dict = {"a": {"b": {"c": 1}}}
    src: dict = {"Merge Strategy": "merge", "a": {"b": {"d": 2}}}
    merger.yaml_merge(dest, src)
    assert dest == {"a": {"b": {"c": 1, "d": 2}}}  # nested dict is replaced, not merged


@pytest.mark.asyncio
async def test_merge_strategy_deep_merge_list_in_list() -> None:
    dest: dict = {"a": [[1, 2], [3, 4]]}
    src: dict = {"Merge Strategy": "deep_merge", "a": [[5, 6]]}
    merger.yaml_merge(dest, src)
    assert dest == {"a": [[1, 2], [3, 4], [5, 6]]}  # new list is appended


@pytest.mark.asyncio
async def test_merge_strategy_deep_merge_nested_dict() -> None:
    dest: dict = {"a": {"b": {"c": 1}}}
    src: dict = {"Merge Strategy": "deep_merge", "a": {"b": {"d": 2}}}
    merger.yaml_merge(dest, src)
    assert dest == {"a": {"b": {"c": 1, "d": 2}}}  # nested dict is merged


@pytest.mark.asyncio
async def test_merge_strategy_append_list_in_list() -> None:
    dest: dict = {"a": [[1, 2], [3, 4]]}
    src: dict = {"Merge Strategy": "append", "a": [[5, 6]]}
    merger.yaml_merge(dest, src)
    assert dest == {"a": [[1, 2], [3, 4], [5, 6]]}  # new list is appended


@pytest.mark.asyncio
async def test_merge_strategy_append_nested_dict() -> None:
    dest: dict = {"a": {"b": {"c": 1}}}
    src: dict = {"Merge Strategy": "append", "a": {"b": {"d": 2}}}
    merger.yaml_merge(dest, src)
    assert dest == {"a": {"b": {"c": 1, "d": 2}}}  # nested dict is merged


@pytest.mark.asyncio
async def test_invalid_strategy() -> None:
    dest: dict = {"a": "1", "b": "2"}
    src: dict = {"Merge Strategy": "invalid_strategy", "a": "3", "b": "2"}
    with pytest.raises(ValueError):
        merger.yaml_merge(dest, src)  # raises ValueError due to invalid strategy


@pytest.mark.asyncio
async def test_case_insensitive_strategy() -> None:
    dest: dict = {"a": "1", "b": "2"}
    src: dict = {"Merge Strategy": "DeEp_MeRgE", "a": "3", "b": "2", "c": [3, 4, 5]}
    merger.yaml_merge(dest, src)
    assert dest == {"a": "3", "b": "2", "c": [3, 4, 5]}  # case insensitive strategy is recognized and applied


@pytest.mark.asyncio
async def test_case_insensitive_strategy_disabled() -> None:
    dest: dict = {"a": "1", "b": "2"}
    src: dict = {"Merge Strategy": "DISABLED", "a": "3", "b": "2"}
    merger.yaml_merge(dest, src)
    assert dest == {"a": "1", "b": "2"}  # dest remains the same as the merge is disabled
    dest: dict = {"a": "1", "b": "2"}
    src: dict = {"Merge Strategy": "disable", "a": "3", "b": "2"}
    merger.yaml_merge(dest, src)
    assert dest == {"a": "1", "b": "2"}  # dest remains the same as the merge is disabled


@pytest.mark.asyncio
async def test_case_insensitive_strategy_merge() -> None:
    dest: dict = {"a": "1", "b": "2"}
    src: dict = {"Merge Strategy": "MERGE", "a": "3", "b": "4"}
    merger.yaml_merge(dest, src)
    assert dest == {"a": "3", "b": "4"}

    dest: dict = {"a": "1", "b": "2"}
    src: dict = {"Merge Strategy": "merge", "a": "3", "b": "4"}
    merger.yaml_merge(dest, src)
    assert dest == {"a": "3", "b": "4"}


@pytest.mark.asyncio
async def test_case_insensitive_strategy_append() -> None:
    dest: dict = {"a": "1", "b": "2"}
    src: dict = {"Merge Strategy": "APPEND", "a": "3", "b": "4", "c": "5"}
    merger.yaml_merge(dest, src)
    assert dest == {"a": "1", "b": "2", "c": "5"}

    dest: dict = {"a": "1", "b": "2"}
    src: dict = {"Merge Strategy": "APPEND", "a": "3", "b": "4", "c": "5"}
    merger.yaml_merge(dest, src)
    assert dest == {"a": "1", "b": "2", "c": "5"}


@pytest.mark.asyncio
async def test_case_insensitive_strategy_deep_merge() -> None:
    dest: dict = {"a": "1", "b": "2", "c": [1, 2, 3, {"d": 4}]}
    src: dict = {"Merge Strategy": "DEEP_MERGE", "a": "3", "b": "2", "c": [3, 4, 5, {"e": 6}]}
    merger.yaml_merge(dest, src)
    assert dest == {"a": "3", "b": "2", "c": [1, 2, 3, {"d": 4}, 4, 5, {"e": 6}]}

    dest: dict = {"a": "1", "b": "2", "c": [1, 2, 3, {"d": 4}]}
    src: dict = {"Merge Strategy": "deep_merge", "a": "3", "b": "2", "c": [3, 4, 5, {"e": 6}]}
    merger.yaml_merge(dest, src)
    assert dest == {"a": "3", "b": "2", "c": [1, 2, 3, {"d": 4}, 4, 5, {"e": 6}]}

    dest: dict = {"a": "1", "b": "2", "c": [1, 2, 3, {"d": 4}]}
    src: dict = {"Merge Strategy": "deep merge", "a": "3", "b": "2", "c": [3, 4, 5, {"e": 6}]}
    merger.yaml_merge(dest, src)
    assert dest == {"a": "3", "b": "2", "c": [1, 2, 3, {"d": 4}, 4, 5, {"e": 6}]}

    dest: dict = {"a": "1", "b": "2", "c": [1, 2, 3, {"d": 4}]}
    src: dict = {"Merge Strategy": "DEEP MERGE", "a": "3", "b": "2", "c": [3, 4, 5, {"e": 6}]}
    merger.yaml_merge(dest, src)
    assert dest == {"a": "3", "b": "2", "c": [1, 2, 3, {"d": 4}, 4, 5, {"e": 6}]}

    dest: dict = {"a": "1", "b": "2", "c": [1, 2, 3, {"d": 4}]}
    src: dict = {"Merge Strategy": "deepmerge", "a": "3", "b": "2", "c": [3, 4, 5, {"e": 6}]}
    merger.yaml_merge(dest, src)
    assert dest == {"a": "3", "b": "2", "c": [1, 2, 3, {"d": 4}, 4, 5, {"e": 6}]}

    dest: dict = {"a": "1", "b": "2", "c": [1, 2, 3, {"d": 4}]}
    src: dict = {"Merge Strategy": "DEEPMERGE", "a": "3", "b": "2", "c": [3, 4, 5, {"e": 6}]}
    merger.yaml_merge(dest, src)
    assert dest == {"a": "3", "b": "2", "c": [1, 2, 3, {"d": 4}, 4, 5, {"e": 6}]}

