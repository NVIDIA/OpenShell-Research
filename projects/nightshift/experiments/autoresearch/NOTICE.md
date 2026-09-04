# Attribution

`workdir/train.py` and `image/prepare.py` are adapted from Andrej Karpathy's
[autoresearch](https://github.com/karpathy/autoresearch), released under the
MIT License, Copyright (c) 2026 Andrej Karpathy. The training code there is in
turn a simplified single-GPU version of [nanochat](https://github.com/karpathy/nanochat).

Changes made for Nightshift are described at the top of each file: hardware
profiles, a TinyStories dataset for CPU, device-generic attention and data
loading, and a machine-readable result for the scorer.

## MIT License (upstream)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
