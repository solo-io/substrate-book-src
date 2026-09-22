# Tiny Slug Service

This sample project turns human-readable titles into URL slugs.

The implementation is intentionally incomplete. A correct `slugify` function must:

- lowercase its input;
- replace each run of spaces or punctuation with one hyphen;
- remove leading and trailing hyphens;
- return an empty string when the input contains no letters or digits.

Run the tests with:

```shell
python3 -m unittest discover -s tests -v
```

