const minimumOpenShellVersion = '0.0.106'

type Version = [number, number, number]

function parseVersion(value: string, label: string): Version {
  const match = value.trim().match(/^v?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$/)
  if (!match) throw new Error(`${label} reported an unsupported version: ${value}`)
  return [Number(match[1]), Number(match[2]), Number(match[3])]
}

function compare(left: Version, right: Version): number {
  for (const difference of [left[0] - right[0], left[1] - right[1], left[2] - right[2]]) {
    if (difference !== 0) return difference
  }
  return 0
}

function format(version: Version): string {
  return version.join('.')
}

export function assertMatchingOpenShellVersions(gatewayValue: string, sdkValue: string): void {
  const gateway = parseVersion(gatewayValue, 'OpenShell gateway')
  const sdk = parseVersion(sdkValue, 'OpenShell TypeScript SDK')
  const minimum = parseVersion(minimumOpenShellVersion, 'minimum OpenShell version')

  if (compare(gateway, minimum) < 0) {
    throw new Error(`OpenShell gateway ${format(gateway)} is older than required ${minimumOpenShellVersion}`)
  }
  if (compare(sdk, minimum) < 0) {
    throw new Error(`OpenShell TypeScript SDK ${format(sdk)} is older than required ${minimumOpenShellVersion}`)
  }
  if (compare(gateway, sdk) !== 0) {
    throw new Error(
      `OpenShell gateway ${format(gateway)} and TypeScript SDK ${format(sdk)} must use the same release`,
    )
  }
}
