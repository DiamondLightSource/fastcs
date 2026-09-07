# CLAUDE.md

Conventions for this repository. Follow these when writing or reviewing code and tests.

## Helper functions and exceptions

- A helper function must **not** be passed a parameter (e.g. `kind`) whose only purpose
  is to be interpolated into the message of an exception it raises. That parameter
  exists purely to serve one or more call-sites' error-reporting needs, which means the
  helper is doing the call-site's job for it.
- If different call-sites need different exceptions raised (different types and/or
  different messages), do not thread a parameter into the helper to cover every case.
  Instead, let the helper raise its own plain/generic exception with no caller-supplied
  wording, and have each call-site `catch` it and `raise ... from ...` with the
  type/message it actually needs.

  ```python
  # Bad: helper takes `kind` purely to phrase its own exception message
  def _check_positive(value: float, kind: str) -> None:
      if value <= 0:
          raise ValueError(f"{kind} must be positive, got {value}")

  _check_positive(period, kind="period")

  # Good: helper raises a plain exception; call-site adds whatever context it needs
  def _check_positive(value: float) -> None:
      if value <= 0:
          raise ValueError(f"must be positive, got {value}")

  try:
      _check_positive(period)
  except ValueError as e:
      raise ConfigError(f"invalid period in trigger config: {e}") from e
  ```

- A helper function must **not** be passed a parameter like `expected` or `skip` that
  tells it about the *arity or shape of the call-site* (e.g. "how many items did you
  expect", "should this check be skipped"). Parameters like these are a sign that the
  check itself belongs in the caller, not the helper. Move the check up:

  ```python
  # Bad: helper is making a decision that belongs to the caller
  def _check_length(items, expected=None):
      if expected is not None and len(items) != expected:
          raise ValueError(...)

  # Good: caller owns the decision, helper just does the one thing it's for
  if len(items) != expected:
      raise ValueError(...)
  _check_length(items)
  ```

  Rule of thumb: a helper's parameters should describe *what it's being asked to
  validate/produce*, never *whether/how the caller wants it validated*.

- The same applies to a parameter that only names *where the data came from* so the
  helper can put it in its message (`source`, `origin`, `context`). The call-site
  already knows that; if it wants it in the error, it should catch the helper's plain
  exception and say so itself. Prefer a generic exception from the helper to a
  parameter that exists only to decorate one.

  ```python
  # Bad: `source` is threaded in purely to phrase the message
  def check_filled(self, source: str | None = None) -> None:
      ...
      raise RuntimeError(f"did not provision from {source}: {missing}")

  # Good: the helper says what it knows
  def check_filled(self) -> None:
      ...
      raise RuntimeError(f"did not provision: {missing}")
  ```

## Typing

- Do **not** annotate a parameter `Any` when a narrower type says what the function
  actually accepts. `Any` in a signature is a promise the function cannot keep: it
  turns an author-time error into a runtime one, or into no error at all.

  ```python
  # Bad: any object at all type checks
  def fill_attribute(self, name: str, datatype: Any = None) -> Attribute: ...

  # Good: only the types an attribute can hold
  def fill_attribute(self, name: str, datatype: type[DType_T] | None = None) -> Attribute: ...
  ```

- This holds especially for callables that will be attached to something already
  typed. IO handed to an existing `Attribute` must be typed with the datatype
  TypeVar (`Getter[DType_T]`, `Setter[DType_T]`), not `Getter[Any]` - otherwise a
  setter taking a `datetime.datetime` type checks against a `float` attribute and only
  fails, if at all, at runtime. Type it so the author is warned where they wrote it.

- A `# type: ignore` or `# pyright: ignore` must be **required**. Before adding one,
  remove it and re-run the type checker: if nothing is reported, it does not belong.
  Before reaching for one at all, look for a signature that makes it unnecessary -
  `**meta: Unpack[Meta]` rather than `**meta: Any` plus an ignore at the call that
  passes them on. Every remaining ignore should be specific (`[reportCallIssue]`, not
  bare) and should have a reason a reader can check.

## Tests: `pytest.raises`

- The `with pytest.raises(...):` block should contain the **minimal code that raises
  the exception** — ideally a single line, and ideally just the call under test.
- Any setup needed to *put the system in a state* where that call will raise must
  happen **outside** and **before** the `pytest.raises` block, not inside it.

  ```python
  # Bad: setup is inside the raises block
  with pytest.raises(ValueError):
      controller = Device()
      controller.configure(bad_value)

  # Good: setup happens first, only the failing call is inside the block
  controller = Device()
  with pytest.raises(ValueError):
      controller.configure(bad_value)
  ```

  This keeps the assertion precise: if setup itself started raising unexpectedly, the
  test should fail with an ordinary traceback, not be masked as a (possibly
  coincidental) pass inside `pytest.raises`.

## Tests: no irrelevant lines

- Every line in a test should be there because it affects the test's outcome, given
  what the test's name says it's checking. If removing a line wouldn't change whether
  the test passes or fails, it doesn't belong.
- Before adding or keeping a line in a test, check it against the test name: does this
  line change the behavior being verified? If not, delete it.

  ```python
  # Bad: post_initialise() doesn't affect this test's assertion
  def test_matching_type_hint_is_satisfied_by_the_decorated_attribute():
      class Device(Controller):
          label: AttrR[str]  # pyright: ignore[reportRedeclaration]

          @attr
          async def label(self) -> str:
              return "x"

      controller = Device()
      controller.post_initialise()  # irrelevant — remove

      assert isinstance(controller.label, AttrR)

  # Good
  def test_matching_type_hint_is_satisfied_by_the_decorated_attribute():
      class Device(Controller):
          label: AttrR[str]  # pyright: ignore[reportRedeclaration]

          @attr
          async def label(self) -> str:
              return "x"

      controller = Device()

      assert isinstance(controller.label, AttrR)
  ```

  This keeps tests readable as documentation: every line is evidence for the claim in
  the test's name, not incidental noise carried over from copy-pasting another test.

## Tests: one behaviour per test

- A test checks **one** thing, and its name says which. Do not put two `pytest.raises`
  blocks, or a failing case and a happy path, in one test: they are separate claims
  about the code, and when the first fails the rest never run.
- Setup shared by the split tests belongs in a fixture, a module-level class, or a
  couple of repeated lines - repeating three lines of setup is cheaper than a test
  that verifies four unrelated things.

  ```python
  # Bad: three claims, one name, and the last two only run if the first passes
  def test_hint_validation():
      controller = HintedController()

      with pytest.raises(RuntimeError, match="never added"):
          controller.check_filled()

      with pytest.raises(RuntimeError, match="expected 'AttrR'"):
          controller.add_attribute("state", AttrW(int))

      controller.add_attribute("state", AttrR(int))
      controller.check_filled()

  # Good: one claim each, each named for what it checks
  def test_a_hint_without_a_datatype_is_promised(): ...
  def test_adding_a_promised_attribute_with_the_wrong_access_mode_raises(): ...
  def test_adding_a_promised_attribute_satisfies_the_declaration(): ...
  ```

## Tests: parametrize instead of near-identical tests

- If two or more tests differ only in the values they use - a different hint, a
  different datatype, a different expected message - write one
  `@pytest.mark.parametrize`d test rather than copies of one body.
- Keep them separate when the *shape* of the test differs, not just its data: a case
  that needs different setup, different assertions, or a different name to make sense
  is a different test.
