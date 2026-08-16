/**
 * Tests for settings store: useSettingsStore.
 * Usage: cd frontend && npx vitest run test/stores/settings.test.js
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

// Mock localStorage (same pattern as auth.test.js / sessions.test.js)
const storeData = {}
global.localStorage = {
  getItem: vi.fn((key) => storeData[key] ?? null),
  setItem: vi.fn((key, value) => { storeData[key] = value }),
  removeItem: vi.fn((key) => { delete storeData[key] }),
}

// Helper: pre-populate saved settings in the format the real store uses
function setSavedSettings(settings) {
  storeData['arknights_rag_settings'] = JSON.stringify(settings)
}

// Mock api
vi.mock('../../src/api', () => ({
  api: {
    getModels: vi.fn(),
  },
}))

import { useSettingsStore } from '../../src/stores/settings'
import { api } from '../../src/api'

describe('useSettingsStore', () => {
  beforeEach(() => {
    Object.keys(storeData).forEach(k => delete storeData[k])
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  describe('initial state', () => {
    it('theme defaults to dark when no saved settings', () => {
      const store = useSettingsStore()
      expect(store.theme).toBe('dark')
    })

    it('currentModel starts empty', () => {
      const store = useSettingsStore()
      expect(store.currentModel).toBe('')
    })

    it('availableModels starts empty', () => {
      const store = useSettingsStore()
      expect(store.availableModels).toEqual([])
    })

    it('loads saved theme from localStorage', () => {
      setSavedSettings({ theme: 'light', currentModel: 'm1' })
      const store = useSettingsStore()
      // loadSettings is called in store constructor
      expect(store.theme).toBe('light')
      expect(store.currentModel).toBe('m1')
    })

    it('falls back to defaults on corrupted localStorage', () => {
      storeData['arknights_rag_settings'] = 'not-valid-json'
      // loadSettings() catches JSON.parse errors and falls back to defaults
      const store = useSettingsStore()
      expect(store.theme).toBe('dark')
      expect(store.currentModel).toBe('')
    })
  })

  describe('toggleTheme', () => {
    it('toggles from dark to light', () => {
      const store = useSettingsStore()
      expect(store.theme).toBe('dark')
      store.toggleTheme()
      expect(store.theme).toBe('light')
    })

    it('toggles from light to dark', () => {
      setSavedSettings({ theme: 'light', currentModel: '' })
      const store = useSettingsStore()
      expect(store.theme).toBe('light')
      store.toggleTheme()
      expect(store.theme).toBe('dark')
    })

    it('toggle saves to localStorage', () => {
      const store = useSettingsStore()
      store.toggleTheme()
      const saved = JSON.parse(localStorage.getItem('arknights_rag_settings'))
      expect(saved.theme).toBe('light')
    })
  })

  describe('setModel', () => {
    it('sets currentModel', () => {
      const store = useSettingsStore()
      store.setModel('deepseek-v4-flash')
      expect(store.currentModel).toBe('deepseek-v4-flash')
    })

    it('saves model to localStorage', () => {
      const store = useSettingsStore()
      store.setModel('model-xyz')
      const saved = JSON.parse(localStorage.getItem('arknights_rag_settings'))
      expect(saved.currentModel).toBe('model-xyz')
    })
  })

  describe('saveSettings', () => {
    it('persists current theme and model', () => {
      const store = useSettingsStore()
      store.setModel('test-model')
      store.toggleTheme() // dark -> light

      const saved = JSON.parse(localStorage.getItem('arknights_rag_settings'))
      expect(saved.theme).toBe('light')
      expect(saved.currentModel).toBe('test-model')
    })
  })

  describe('loadModels', () => {
    it('fetches models and populates availableModels', async () => {
      api.getModels.mockResolvedValue({
        models: [
          { id: 'deepseek-v4-flash', name: 'DeepSeek V4 Flash' },
          { id: 'qwen-32b', name: 'Qwen 32B' },
        ],
        default: 'deepseek-v4-flash',
      })

      const store = useSettingsStore()
      await store.loadModels()

      expect(store.availableModels.length).toBe(2)
      expect(store.availableModels[0].id).toBe('deepseek-v4-flash')
    })

    it('sets currentModel to default if not already set', async () => {
      api.getModels.mockResolvedValue({
        models: [{ id: 'm1' }],
        default: 'm1',
      })

      const store = useSettingsStore()
      expect(store.currentModel).toBe('')
      await store.loadModels()
      expect(store.currentModel).toBe('m1')
    })

    it('does not override currentModel if already set', async () => {
      api.getModels.mockResolvedValue({
        models: [{ id: 'm1' }],
        default: 'm1',
      })

      const store = useSettingsStore()
      store.setModel('custom-model')
      await store.loadModels()
      // Should keep the existing value
      expect(store.currentModel).toBe('custom-model')
    })

    it('falls back to first model if no default', async () => {
      api.getModels.mockResolvedValue({
        models: [{ id: 'first-model' }],
      })

      const store = useSettingsStore()
      await store.loadModels()
      expect(store.currentModel).toBe('first-model')
    })

    it('handles API error gracefully', async () => {
      api.getModels.mockRejectedValue(new Error('network error'))
      const store = useSettingsStore()

      // Should not throw
      await expect(store.loadModels()).resolves.toBeUndefined()
      expect(store.availableModels).toEqual([])
    })

    it('handles API returning empty models', async () => {
      api.getModels.mockResolvedValue({ models: [] })
      const store = useSettingsStore()
      await store.loadModels()
      expect(store.availableModels).toEqual([])
      // currentModel stays empty since there are no models
      expect(store.currentModel).toBe('')
    })
  })
})
