# Contributing

## Terminal messages

Write new terminal messages in English in the Python scripts. Put the matching Chinese translation in `zh_messages.json` using the complete English message as its key.

For a message with changing values, use `localized_format` and named placeholders:

```python
print(localized_format(
    "Moved {count} files to {destination}",
    count=count,
    destination=destination,
))
```

The Chinese entry must use the same placeholder names. Keep paths, filenames, counts, and dates out of the translated text so they are inserted after translation. Translate the whole message; do not add separate entries for its prefix, punctuation, or suffix. Use `localized_message` for fixed labels such as generated category folder names.

Some Chinese words remain in recognition rules because they identify real screenshots, apps, Finder tags, and metadata. `en_messages.json` is retained as a fallback for those terms in English terminal output.

Run `python3 -m unittest discover -s tests -v` before submitting a change. The localization tests check both languages and matching template placeholders.
