// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { EnvHttpProxyAgent, setGlobalDispatcher } from "undici";

/** Honor the sandbox's proxy after loading Pi and its HTTP dependencies. */
export function configureProxy(): void {
  setGlobalDispatcher(
    new EnvHttpProxyAgent({ proxyTunnel: true, allowH2: false }),
  );
}
