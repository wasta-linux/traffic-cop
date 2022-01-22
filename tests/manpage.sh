#!/bin/bash

# Preview and convert MD file to manpage.
# Ref:
#   https://www.howtogeek.com/682871/how-to-create-a-man-page-on-linux/
#   https://github.com/jgm/pandoc/issues/5404

tests_dir=$(realpath $(dirname "${0}"))
root_dir=$(dirname "$tests_dir")
app_name=$(basename "$root_dir")
draft="${root_dir}/data/man/${app_name}.1.md"
outfile_name="${draft%.*}"
outfile_name="${outfile_name##*/}"
outfile="${root_dir}/doc/${outfile_name}"
debfile="${root_dir}/debian/${app_name}.manpages"
if [[ -z "$1" || "$1" == 'preview' || "$1" == 'p' ]]; then
    pandoc -s -f markdown-smart -t man "$draft" | man -l -
elif [[ "$1" == 'convert' || "$1" == 'c' ]]; then
    mkdir -p "${root_dir}/doc"
    pandoc -s -f markdown-smart -t man -o "$outfile" "$draft"
    echo "doc/${outfile_name}" > "$debfile"
fi
