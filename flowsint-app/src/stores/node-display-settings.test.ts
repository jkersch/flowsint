import { describe, it, expect } from 'vitest'
import {
  DEFAULT_COLORS,
  ITEM_TYPES,
  TYPE_TO_ICON,
  mergePersistedSettings,
  useNodesDisplaySettings
} from './node-display-settings'
import * as LucideIcons from 'lucide-react'

const THREAT_INTEL_TYPES = ['phishingpage', 'phishkit', 'threatactor', 'registrar', 'isp'] as const

describe('node display defaults', () => {
  it('gives every item type an icon that exists in lucide-react', () => {
    for (const type of ITEM_TYPES) {
      const icon = TYPE_TO_ICON[type]
      expect(icon, type).toBeDefined()
      expect(LucideIcons[icon], `${type} -> ${icon}`).toBeDefined()
    }
  })

  it('gives every item type a hex color', () => {
    for (const type of ITEM_TYPES) {
      expect(DEFAULT_COLORS[type], type).toMatch(/^#[0-9A-Fa-f]{6}$/)
    }
  })

  it.each(THREAT_INTEL_TYPES)('includes threat intel type %s', (type) => {
    expect(ITEM_TYPES).toContain(type)
  })

  it('lists each item type once', () => {
    expect(new Set(ITEM_TYPES).size).toBe(ITEM_TYPES.length)
  })
})

describe('mergePersistedSettings', () => {
  const current = () => useNodesDisplaySettings.getInitialState()

  it('fills colors for types added after settings were saved', () => {
    const savedBeforeThreatIntel = Object.fromEntries(
      Object.entries(DEFAULT_COLORS).filter(
        ([type]) => !(THREAT_INTEL_TYPES as readonly string[]).includes(type)
      )
    )
    const merged = mergePersistedSettings({ colors: savedBeforeThreatIntel }, current())

    for (const type of THREAT_INTEL_TYPES) {
      expect(merged.colors[type]).toBe(DEFAULT_COLORS[type])
    }
  })

  it('keeps colors and icons the user saved', () => {
    const merged = mergePersistedSettings(
      { colors: { domain: '#000000' }, customIcons: { domain: 'Anchor' } },
      current()
    )
    expect(merged.colors.domain).toBe('#000000')
    expect(merged.colors.ip).toBe(DEFAULT_COLORS.ip)
    expect(merged.customIcons).toEqual({ domain: 'Anchor' })
  })

  it('keeps the store actions', () => {
    const merged = mergePersistedSettings({ colors: {} }, current())
    expect(typeof merged.setColor).toBe('function')
    expect(typeof merged.getIcon).toBe('function')
  })

  it('falls back to defaults when nothing was persisted', () => {
    for (const persisted of [undefined, null, {}]) {
      expect(mergePersistedSettings(persisted, current()).colors).toEqual(DEFAULT_COLORS)
    }
  })
})
