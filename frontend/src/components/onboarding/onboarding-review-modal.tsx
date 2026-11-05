import { useState } from 'react'
import {
  Building2,
  CheckCircle2,
  FileText,
  HelpCircle,
  Layers,
  MessageSquare,
  Package,
  Plus,
  Rocket,
  ShieldAlert,
  Sparkles,
  Trash2,
  X,
} from 'lucide-react'
import type { StagedOnboardingDraft } from '@/lib/uploads'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Card, CardBody, CardHeader } from '@/components/ui/card'
import { Input, Textarea } from '@/components/ui/input'
import { Alert } from '@/components/ui/feedback'
import { useToast } from '@/components/ui/toast'

interface OnboardingReviewModalProps {
  draft: StagedOnboardingDraft
  onClose: () => void
  onConfirm: (confirmedDraft: StagedOnboardingDraft) => Promise<void>
  publishing?: boolean
}

type TabType = 'brand' | 'menu' | 'catalog' | 'faqs' | 'intents' | 'guardrails'

function normalizeDraft(d: StagedOnboardingDraft): StagedOnboardingDraft {
  return {
    ...d,
    offerings: d.offerings || [],
    faqs: d.faqs || [],
    menu_items: d.menu_items || [],
    intents: d.intents || [],
    knowledge_articles: d.knowledge_articles || [],
    guardrails: {
      tone: d.guardrails?.tone || 'consultative, polished, and professional',
      never_state: d.guardrails?.never_state || [],
      escalation_triggers: d.guardrails?.escalation_triggers,
      escalation_rules: d.guardrails?.escalation_rules,
      escalation_message: d.guardrails?.escalation_message,
    },
  }
}

export function OnboardingReviewModal({
  draft: initialDraft,
  onClose,
  onConfirm,
  publishing = false,
}: OnboardingReviewModalProps) {
  const toast = useToast()
  const [draft, setDraft] = useState<StagedOnboardingDraft>(() => normalizeDraft(initialDraft))
  const [activeTab, setActiveTab] = useState<TabType>('catalog')

  // Brand Mutator
  const patchBrand = (field: string, val: string) => {
    setDraft((prev) => ({
      ...prev,
      brand: { ...prev.brand, [field]: val },
    }))
  }

  // Menu Mutator
  const updateMenuItem = (index: number, field: string, val: string) => {
    setDraft((prev) => {
      const updated = [...prev.menu_items]
      updated[index] = { ...updated[index], [field]: val }
      return { ...prev, menu_items: updated }
    })
  }

  const removeMenuItem = (index: number) => {
    setDraft((prev) => ({
      ...prev,
      menu_items: prev.menu_items.filter((_, i) => i !== index),
    }))
  }

  // Catalog / Offerings Mutator
  const updateOffering = (index: number, field: string, val: string) => {
    setDraft((prev) => {
      const updated = [...prev.offerings]
      updated[index] = { ...updated[index], [field]: val }
      return { ...prev, offerings: updated }
    })
  }

  const updateOfferingAttr = (offIndex: number, attrKey: string, val: string) => {
    setDraft((prev) => {
      const updated = [...prev.offerings]
      const currentAttrs = updated[offIndex].attrs || {}
      updated[offIndex] = {
        ...updated[offIndex],
        attrs: { ...currentAttrs, [attrKey]: val },
      }
      return { ...prev, offerings: updated }
    })
  }

  const removeOffering = (index: number) => {
    setDraft((prev) => ({
      ...prev,
      offerings: prev.offerings.filter((_, i) => i !== index),
    }))
  }

  // FAQs Mutator
  const updateFaq = (index: number, field: string, val: string) => {
    setDraft((prev) => {
      const updated = [...prev.faqs]
      updated[index] = { ...updated[index], [field]: val }
      return { ...prev, faqs: updated }
    })
  }

  const removeFaq = (index: number) => {
    setDraft((prev) => ({
      ...prev,
      faqs: prev.faqs.filter((_, i) => i !== index),
    }))
  }

  const updateIntent = (index: number, field: string, val: string) => {
    setDraft((prev) => {
      const updated = [...(prev.intents || [])]
      updated[index] = { ...updated[index], [field]: val }
      return { ...prev, intents: updated }
    })
  }

  const removeIntent = (index: number) => {
    setDraft((prev) => ({
      ...prev,
      intents: (prev.intents || []).filter((_, i) => i !== index),
    }))
  }

  const handleConfirm = async () => {
    if (!draft.brand.name.trim()) {
      toast.push('Company brand name cannot be empty', 'error')
      setActiveTab('brand')
      return
    }
    await onConfirm(draft)
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/60 p-4 backdrop-blur-sm">
      <div className="relative flex max-h-[92vh] w-full max-w-5xl flex-col rounded-xl border border-surface-line bg-surface-card shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-surface-line px-6 py-4">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-teal-500/10 text-teal-600">
              <Sparkles className="h-5 w-5" />
            </div>
            <div>
              <div className="flex items-center gap-2 flex-wrap">
                <h3 className="text-base font-semibold text-surface-fg">
                  Review & Confirm Bifurcated Onboarding Data
                </h3>
                <Badge variant="outline" className="uppercase tracking-wider">
                  {draft.vertical.replace('_', ' ')}
                </Badge>
                <Badge
                  variant="outline"
                  className={
                    draft.extraction_mode === 'kit'
                      ? 'border-teal-500/40 text-teal-700 bg-teal-500/10'
                      : 'border-sky-500/40 text-sky-700 bg-sky-500/10'
                  }
                >
                  {draft.extraction_mode === 'kit' ? 'Kit parse' : 'General extraction'}
                </Badge>
              </div>
              <p className="text-xs text-surface-muted">
                From {(draft.source_files || []).join(', ') || 'upload'} • {draft.offerings.length} offerings •{' '}
                {draft.faqs.length} FAQs • {(draft.intents || []).length} intents
              </p>
              {draft.warning ? (
                <p className="text-xs text-amber-700 mt-1">{draft.warning}</p>
              ) : null}
            </div>
          </div>
          <button
            onClick={onClose}
            className="rounded-lg p-1.5 text-surface-muted hover:bg-surface-elevated hover:text-surface-fg transition"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Tab Navigation */}
        <div className="flex border-b border-surface-line bg-surface-bg/40 px-6">
          <button
            onClick={() => setActiveTab('catalog')}
            className={`flex items-center gap-2 border-b-2 px-4 py-3 text-xs font-medium transition ${
              activeTab === 'catalog'
                ? 'border-teal-500 text-teal-600'
                : 'border-transparent text-surface-muted hover:text-surface-fg'
            }`}
          >
            <Package className="h-4 w-4" />
            Catalog & Properties ({draft.offerings.length})
          </button>
          <button
            onClick={() => setActiveTab('menu')}
            className={`flex items-center gap-2 border-b-2 px-4 py-3 text-xs font-medium transition ${
              activeTab === 'menu'
                ? 'border-teal-500 text-teal-600'
                : 'border-transparent text-surface-muted hover:text-surface-fg'
            }`}
          >
            <MessageSquare className="h-4 w-4" />
            WhatsApp Menu ({draft.menu_items.length})
          </button>
          <button
            onClick={() => setActiveTab('faqs')}
            className={`flex items-center gap-2 border-b-2 px-4 py-3 text-xs font-medium transition ${
              activeTab === 'faqs'
                ? 'border-teal-500 text-teal-600'
                : 'border-transparent text-surface-muted hover:text-surface-fg'
            }`}
          >
            <HelpCircle className="h-4 w-4" />
            FAQs & Knowledge ({draft.faqs.length})
          </button>
          <button
            onClick={() => setActiveTab('intents')}
            className={`flex items-center gap-2 border-b-2 px-4 py-3 text-xs font-medium transition ${
              activeTab === 'intents'
                ? 'border-teal-500 text-teal-600'
                : 'border-transparent text-surface-muted hover:text-surface-fg'
            }`}
          >
            <Layers className="h-4 w-4" />
            Intents ({(draft.intents || []).length})
          </button>
          <button
            onClick={() => setActiveTab('brand')}
            className={`flex items-center gap-2 border-b-2 px-4 py-3 text-xs font-medium transition ${
              activeTab === 'brand'
                ? 'border-teal-500 text-teal-600'
                : 'border-transparent text-surface-muted hover:text-surface-fg'
            }`}
          >
            <Building2 className="h-4 w-4" />
            Brand Profile
          </button>
          <button
            onClick={() => setActiveTab('guardrails')}
            className={`flex items-center gap-2 border-b-2 px-4 py-3 text-xs font-medium transition ${
              activeTab === 'guardrails'
                ? 'border-teal-500 text-teal-600'
                : 'border-transparent text-surface-muted hover:text-surface-fg'
            }`}
          >
            <ShieldAlert className="h-4 w-4" />
            Guardrails & Persona
          </button>
        </div>

        {/* Content Area */}
        <div className="flex-1 overflow-y-auto p-6 space-y-4">
          {/* TAB 1: CATALOG & OFFERINGS */}
          {activeTab === 'catalog' && (
            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <div>
                  <h4 className="text-sm font-semibold text-surface-fg">
                    Extracted Offerings / Real Estate Properties
                  </h4>
                  <p className="text-xs text-surface-muted">
                    These items will be inserted into your active catalog and indexed for conversational product inquiries.
                  </p>
                </div>
              </div>

              {draft.offerings.length === 0 ? (
                <div className="rounded-lg border border-dashed border-surface-line p-8 text-center text-xs text-surface-muted">
                  No catalog items found in the document. You can add items or continue.
                </div>
              ) : (
                <div className="space-y-3">
                  {draft.offerings.map((item, idx) => (
                    <div
                      key={idx}
                      className="rounded-lg border border-surface-line bg-surface-bg/50 p-4 transition hover:border-teal-500/40"
                    >
                      <div className="flex items-start justify-between gap-4">
                        <div className="grid flex-1 grid-cols-1 gap-3 sm:grid-cols-3">
                          <div>
                            <label className="text-[10px] font-semibold uppercase tracking-wider text-surface-muted">
                              Item / Property Name
                            </label>
                            <Input
                              value={item.name}
                              onChange={(e) => updateOffering(idx, 'name', e.target.value)}
                              className="mt-1 text-xs"
                            />
                          </div>
                          <div>
                            <label className="text-[10px] font-semibold uppercase tracking-wider text-surface-muted">
                              Category / Type
                            </label>
                            <Input
                              value={item.category}
                              onChange={(e) => updateOffering(idx, 'category', e.target.value)}
                              className="mt-1 text-xs"
                            />
                          </div>
                          <div>
                            <label className="text-[10px] font-semibold uppercase tracking-wider text-surface-muted">
                              Price / Pricing Range
                            </label>
                            <Input
                              value={item.price}
                              onChange={(e) => updateOffering(idx, 'price', e.target.value)}
                              className="mt-1 text-xs"
                            />
                          </div>
                        </div>
                        <button
                          type="button"
                          onClick={() => removeOffering(idx)}
                          className="mt-5 rounded p-1.5 text-surface-muted hover:bg-rose-500/10 hover:text-rose-600 transition"
                          title="Remove item"
                        >
                          <Trash2 className="h-4 w-4" />
                        </button>
                      </div>

                      {/* Attributes (BHK, sqft, location, amenities, rera) */}
                      {item.attrs && Object.keys(item.attrs).length > 0 && (
                        <div className="mt-3 grid grid-cols-2 gap-2 border-t border-surface-line/60 pt-3 sm:grid-cols-4">
                          {Object.entries(item.attrs).map(([k, val]) => (
                            <div key={k} className="text-xs">
                              <span className="text-[10px] font-medium uppercase text-surface-muted">
                                {k.replace('_', ' ')}:
                              </span>
                              <input
                                type="text"
                                value={val}
                                onChange={(e) => updateOfferingAttr(idx, k, e.target.value)}
                                className="mt-0.5 w-full rounded border border-surface-line/80 bg-surface-card px-2 py-1 text-xs text-surface-fg"
                              />
                            </div>
                          ))}
                        </div>
                      )}

                      <div className="mt-3">
                        <label className="text-[10px] font-semibold uppercase tracking-wider text-surface-muted">
                          Detailed Description (for Bot RAG Retrieval)
                        </label>
                        <textarea
                          rows={2}
                          value={item.description}
                          onChange={(e) => updateOffering(idx, 'description', e.target.value)}
                          className="mt-1 w-full rounded border border-surface-line/80 bg-surface-card p-2 text-xs text-surface-fg"
                        />
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* TAB 2: WHATSAPP MENU */}
          {activeTab === 'menu' && (
            <div className="space-y-4">
              <div>
                <h4 className="text-sm font-semibold text-surface-fg">
                  WhatsApp Interactive List Navigation
                </h4>
                <p className="text-xs text-surface-muted">
                  Meta WhatsApp API strictly limits Title to $\le$ 24 characters and Description to $\le$ 72 characters.
                </p>
              </div>

              <div className="space-y-3">
                {draft.menu_items.map((item, idx) => {
                  const titleTooLong = item.title.length > 24
                  const descTooLong = item.description.length > 72
                  return (
                    <div
                      key={idx}
                      className="flex items-center gap-3 rounded-lg border border-surface-line bg-surface-bg/50 p-3"
                    >
                      <div className="flex-1 grid grid-cols-1 gap-2 sm:grid-cols-4">
                        <div>
                          <div className="flex justify-between">
                            <label className="text-[10px] font-semibold uppercase text-surface-muted">
                              Title
                            </label>
                            <span
                              className={`text-[10px] ${
                                titleTooLong ? 'font-bold text-rose-600' : 'text-surface-muted'
                              }`}
                            >
                              {item.title.length}/24
                            </span>
                          </div>
                          <Input
                            value={item.title}
                            onChange={(e) => updateMenuItem(idx, 'title', e.target.value)}
                            className={`mt-1 text-xs ${titleTooLong ? 'border-rose-500' : ''}`}
                          />
                        </div>
                        <div className="sm:col-span-2">
                          <div className="flex justify-between">
                            <label className="text-[10px] font-semibold uppercase text-surface-muted">
                              Description
                            </label>
                            <span
                              className={`text-[10px] ${
                                descTooLong ? 'font-bold text-rose-600' : 'text-surface-muted'
                              }`}
                            >
                              {item.description.length}/72
                            </span>
                          </div>
                          <Input
                            value={item.description}
                            onChange={(e) => updateMenuItem(idx, 'description', e.target.value)}
                            className={`mt-1 text-xs ${descTooLong ? 'border-rose-500' : ''}`}
                          />
                        </div>
                        <div>
                          <label className="text-[10px] font-semibold uppercase text-surface-muted">
                            Action
                          </label>
                          <Input
                            value={item.action}
                            onChange={(e) => updateMenuItem(idx, 'action', e.target.value)}
                            className="mt-1 text-xs"
                          />
                        </div>
                      </div>
                      <button
                        type="button"
                        onClick={() => removeMenuItem(idx)}
                        className="rounded p-1.5 text-surface-muted hover:bg-rose-500/10 hover:text-rose-600 transition"
                      >
                        <Trash2 className="h-4 w-4" />
                      </button>
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          {/* TAB 3: FAQS & KNOWLEDGE */}
          {activeTab === 'faqs' && (
            <div className="space-y-4">
              <div>
                <h4 className="text-sm font-semibold text-surface-fg">
                  Extracted FAQs & Knowledge Base
                </h4>
                <p className="text-xs text-surface-muted">
                  These verified Q&A pairs will be embedded into your tenant vector search index for accurate AI answers.
                </p>
              </div>

              <div className="space-y-3">
                {draft.faqs.map((faq, idx) => (
                  <div
                    key={idx}
                    className="rounded-lg border border-surface-line bg-surface-bg/50 p-4 space-y-2"
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="flex-1">
                        <label className="text-[10px] font-semibold uppercase tracking-wider text-surface-muted">
                          Customer Question
                        </label>
                        <Input
                          value={faq.question}
                          onChange={(e) => updateFaq(idx, 'question', e.target.value)}
                          className="mt-1 text-xs font-medium"
                        />
                      </div>
                      <div className="w-36">
                        <label className="text-[10px] font-semibold uppercase tracking-wider text-surface-muted">
                          Category
                        </label>
                        <Input
                          value={faq.category}
                          onChange={(e) => updateFaq(idx, 'category', e.target.value)}
                          className="mt-1 text-xs"
                        />
                      </div>
                      <button
                        type="button"
                        onClick={() => removeFaq(idx)}
                        className="mt-5 rounded p-1.5 text-surface-muted hover:bg-rose-500/10 hover:text-rose-600 transition"
                      >
                        <Trash2 className="h-4 w-4" />
                      </button>
                    </div>
                    <div>
                      <label className="text-[10px] font-semibold uppercase tracking-wider text-surface-muted">
                        Verified Official Answer
                      </label>
                      <textarea
                        rows={2}
                        value={faq.answer}
                        onChange={(e) => updateFaq(idx, 'answer', e.target.value)}
                        className="mt-1 w-full rounded border border-surface-line/80 bg-surface-card p-2 text-xs text-surface-fg"
                      />
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* TAB: INTENTS (feeds Intents Editor on publish) */}
          {activeTab === 'intents' && (
            <div className="space-y-4">
              <div>
                <h4 className="text-sm font-semibold text-surface-fg">Intents for Intents Editor</h4>
                <p className="text-xs text-surface-muted">
                  These answers publish into the tenant draft (technologies, careers, service_enquiry, pricing, etc.).
                </p>
              </div>
              {(draft.intents || []).length === 0 ? (
                <div className="rounded-lg border border-dashed border-surface-line p-8 text-center text-xs text-surface-muted">
                  No intents extracted. Catalog-linked service/pricing intents are added on publish when offerings exist.
                </div>
              ) : (
                <div className="space-y-3">
                  {(draft.intents || []).map((intent, idx) => (
                    <div
                      key={`${intent.name}-${idx}`}
                      className="rounded-lg border border-surface-line bg-surface-bg/50 p-4 space-y-2"
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div className="grid flex-1 grid-cols-1 gap-3 sm:grid-cols-2">
                          <div>
                            <label className="text-[10px] font-semibold uppercase tracking-wider text-surface-muted">
                              Intent name
                            </label>
                            <Input
                              value={intent.name}
                              onChange={(e) => updateIntent(idx, 'name', e.target.value)}
                              className="mt-1 text-xs font-medium"
                            />
                          </div>
                          <div>
                            <label className="text-[10px] font-semibold uppercase tracking-wider text-surface-muted">
                              Keywords (comma-separated)
                            </label>
                            <Input
                              value={(intent.keywords || []).join(', ')}
                              onChange={(e) => {
                                const kws = e.target.value
                                  .split(',')
                                  .map((s) => s.trim())
                                  .filter(Boolean)
                                setDraft((prev) => {
                                  const updated = [...(prev.intents || [])]
                                  updated[idx] = { ...updated[idx], keywords: kws }
                                  return { ...prev, intents: updated }
                                })
                              }}
                              className="mt-1 text-xs"
                            />
                          </div>
                        </div>
                        <button
                          type="button"
                          onClick={() => removeIntent(idx)}
                          className="mt-5 rounded p-1.5 text-surface-muted hover:bg-rose-500/10 hover:text-rose-600 transition"
                        >
                          <Trash2 className="h-4 w-4" />
                        </button>
                      </div>
                      <div>
                        <label className="text-[10px] font-semibold uppercase tracking-wider text-surface-muted">
                          Bot answer
                        </label>
                        <textarea
                          rows={4}
                          value={intent.answer || ''}
                          onChange={(e) => updateIntent(idx, 'answer', e.target.value)}
                          className="mt-1 w-full rounded border border-surface-line/80 bg-surface-card p-2 text-xs text-surface-fg"
                        />
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* TAB 4: BRAND PROFILE */}
          {activeTab === 'brand' && (
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div>
                <label className="text-xs font-semibold text-surface-fg">Company Brand Name</label>
                <Input
                  value={draft.brand.name}
                  onChange={(e) => patchBrand('name', e.target.value)}
                  className="mt-1 text-xs"
                />
              </div>
              <div>
                <label className="text-xs font-semibold text-surface-fg">AI Bot Assistant Name</label>
                <Input
                  value={draft.brand.bot_name}
                  onChange={(e) => patchBrand('bot_name', e.target.value)}
                  className="mt-1 text-xs"
                />
              </div>
              <div className="sm:col-span-2">
                <label className="text-xs font-semibold text-surface-fg">Brand Tagline / Value Proposition</label>
                <Input
                  value={draft.brand.tagline}
                  onChange={(e) => patchBrand('tagline', e.target.value)}
                  className="mt-1 text-xs"
                />
              </div>
              <div>
                <label className="text-xs font-semibold text-surface-fg">Support Email</label>
                <Input
                  value={draft.brand.support_email}
                  onChange={(e) => patchBrand('support_email', e.target.value)}
                  className="mt-1 text-xs"
                />
              </div>
              <div>
                <label className="text-xs font-semibold text-surface-fg">Support Helpline Phone</label>
                <Input
                  value={draft.brand.support_phone}
                  onChange={(e) => patchBrand('support_phone', e.target.value)}
                  className="mt-1 text-xs"
                />
              </div>
              <div>
                <label className="text-xs font-semibold text-surface-fg">Official Website</label>
                <Input
                  value={draft.brand.website}
                  onChange={(e) => patchBrand('website', e.target.value)}
                  className="mt-1 text-xs"
                />
              </div>
              <div>
                <label className="text-xs font-semibold text-surface-fg">Working Hours</label>
                <Input
                  value={draft.business_hours.raw || '09:30 AM - 06:30 PM (Mon-Sat)'}
                  onChange={(e) =>
                    setDraft((prev) => ({
                      ...prev,
                      business_hours: { ...prev.business_hours, raw: e.target.value },
                    }))
                  }
                  className="mt-1 text-xs"
                />
              </div>
            </div>
          )}

          {/* TAB 5: GUARDRAILS */}
          {activeTab === 'guardrails' && (
            <div className="space-y-4">
              <div>
                <label className="text-xs font-semibold text-surface-fg">Bot Tone & Persona</label>
                <Input
                  value={draft.guardrails.tone}
                  onChange={(e) =>
                    setDraft((prev) => ({
                      ...prev,
                      guardrails: { ...prev.guardrails, tone: e.target.value },
                    }))
                  }
                  className="mt-1 text-xs"
                />
              </div>
              <div>
                <label className="text-xs font-semibold text-surface-fg">
                  Strict Guardrails (What the bot must NEVER do)
                </label>
                <div className="mt-2 space-y-2">
                  {draft.guardrails.never_state.map((rule, rIdx) => (
                    <div key={rIdx} className="flex items-center gap-2">
                      <span className="text-xs text-rose-500 font-bold">•</span>
                      <Input
                        value={rule}
                        onChange={(e) => {
                          const updated = [...draft.guardrails.never_state]
                          updated[rIdx] = e.target.value
                          setDraft((prev) => ({
                            ...prev,
                            guardrails: { ...prev.guardrails, never_state: updated },
                          }))
                        }}
                        className="text-xs flex-1"
                      />
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between border-t border-surface-line bg-surface-card px-6 py-4">
          <Button variant="ghost" onClick={onClose} disabled={publishing}>
            Cancel / Discard
          </Button>
          <div className="flex items-center gap-3">
            <span className="text-xs text-surface-muted">
              {draft.offerings.length} items • {draft.faqs.length} FAQs ready to publish
            </span>
            <Button
              variant="primary"
              loading={publishing}
              onClick={handleConfirm}
              icon={<Rocket className="h-4 w-4" />}
            >
              Confirm & Publish to Live Bot
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}

