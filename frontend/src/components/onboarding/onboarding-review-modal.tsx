import { useState, type ChangeEvent } from 'react'
import {
  Building2,
  HelpCircle,
  Layers,
  Loader2,
  MessageSquare,
  Package,
  Rocket,
  ShieldAlert,
  Sparkles,
  Trash2,
  X,
} from 'lucide-react'
import type { StagedOnboardingDraft } from '@/lib/uploads'
import { cn } from '@/lib/cn'
import { GhostButton, PrimaryButton } from '@/components/remake/ui'
import { Modal } from '@/components/ui/modal'
import { useToast } from '@/components/ui/toast'

interface OnboardingReviewModalProps {
  draft: StagedOnboardingDraft
  onClose: () => void
  onConfirm: (confirmedDraft: StagedOnboardingDraft) => Promise<void>
  publishing?: boolean
}

type TabType = 'brand' | 'menu' | 'catalog' | 'faqs' | 'intents' | 'guardrails'

/** The modal's dense editors use 10px uppercase labels, not the standard
 *  field label — these mini inputs keep that look on the token layer. */
function MiniInput({
  value,
  onChange,
  className,
  invalid,
}: {
  value: string
  onChange: (e: ChangeEvent<HTMLInputElement>) => void
  className?: string
  invalid?: boolean
}) {
  return (
    <input
      value={value}
      onChange={onChange}
      className={cn(
        'w-full rounded-lg border bg-inputbg px-2.5 py-1.5 text-xs text-tx outline-none transition placeholder:text-txfaint focus:border-acc',
        invalid ? 'border-badline' : 'border-line',
        className,
      )}
    />
  )
}

function MiniArea({
  value,
  onChange,
  rows,
}: {
  value: string
  onChange: (e: ChangeEvent<HTMLTextAreaElement>) => void
  rows: number
}) {
  return (
    <textarea
      rows={rows}
      value={value}
      onChange={onChange}
      className="mt-1 w-full rounded-lg border border-line bg-inputbg p-2 text-xs text-tx outline-none transition focus:border-acc"
    />
  )
}

function MiniLabel({ children }: { children: string }) {
  return (
    <span className="text-[10px] font-semibold uppercase tracking-wider text-txmuted">
      {children}
    </span>
  )
}

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

  const tabs: { id: TabType; label: string; icon: typeof Package }[] = [
    { id: 'catalog', label: `Catalog & Properties (${draft.offerings.length})`, icon: Package },
    { id: 'menu', label: `WhatsApp Menu (${draft.menu_items.length})`, icon: MessageSquare },
    { id: 'faqs', label: `FAQs & Knowledge (${draft.faqs.length})`, icon: HelpCircle },
    { id: 'intents', label: `Intents (${(draft.intents || []).length})`, icon: Layers },
    { id: 'brand', label: 'Brand Profile', icon: Building2 },
    { id: 'guardrails', label: 'Guardrails & Persona', icon: ShieldAlert },
  ]

  return (
    <Modal
      onClose={onClose}
      persistent={publishing}
      className="relative flex max-h-[92vh] w-full max-w-5xl flex-col rounded-panel border border-line bg-card shadow-2xl"
    >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-line px-6 py-4">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-accsoft text-acc">
              <Sparkles className="h-5 w-5" />
            </div>
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <h3 className="text-base font-semibold text-tx">
                  Review &amp; Confirm Bifurcated Onboarding Data
                </h3>
                <span className="inline-flex rounded-full border border-line px-2 py-0.5 text-2xs font-medium uppercase tracking-wider text-txmuted">
                  {draft.vertical.replace('_', ' ')}
                </span>
                <span
                  className={
                    draft.extraction_mode === 'kit'
                      ? 'inline-flex rounded-full border border-acc/40 bg-accsoft px-2 py-0.5 text-2xs font-medium text-acc'
                      : 'inline-flex rounded-full border border-chipwebink/40 bg-chipwebbg px-2 py-0.5 text-2xs font-medium text-chipwebink'
                  }
                >
                  {draft.extraction_mode === 'kit' ? 'Kit parse' : 'General extraction'}
                </span>
              </div>
              <p className="text-xs text-txmuted">
                From {(draft.source_files || []).join(', ') || 'upload'} • {draft.offerings.length} offerings •{' '}
                {draft.faqs.length} FAQs • {(draft.intents || []).length} intents
              </p>
              {draft.warning ? (
                <p className="mt-1 text-xs text-attink">{draft.warning}</p>
              ) : null}
            </div>
          </div>
          <button
            onClick={onClose}
            aria-label="Close"
            className="rounded-lg p-1.5 text-txmuted transition hover:bg-paper hover:text-tx"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Tab Navigation */}
        <div className="flex border-b border-line bg-paper/60 px-6">
          {tabs.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`flex items-center gap-2 border-b-2 px-4 py-3 text-xs font-medium transition ${
                activeTab === tab.id
                  ? 'border-acc text-acc'
                  : 'border-transparent text-txmuted hover:text-tx'
              }`}
            >
              <tab.icon className="h-4 w-4" />
              {tab.label}
            </button>
          ))}
        </div>

        {/* Content Area */}
        <div className="flex-1 space-y-4 overflow-y-auto p-6">
          {/* TAB 1: CATALOG & OFFERINGS */}
          {activeTab === 'catalog' && (
            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <div>
                  <h4 className="text-sm font-semibold text-tx">
                    Extracted Offerings / Real Estate Properties
                  </h4>
                  <p className="text-xs text-txmuted">
                    These items will be inserted into your active catalog and indexed for conversational product inquiries.
                  </p>
                </div>
              </div>

              {draft.offerings.length === 0 ? (
                <div className="rounded-panel border border-dashed border-line p-8 text-center text-xs text-txmuted">
                  No catalog items found in the document. You can add items or continue.
                </div>
              ) : (
                <div className="space-y-3">
                  {draft.offerings.map((item, idx) => (
                    <div
                      key={idx}
                      className="rounded-panel border border-line bg-paper p-4 transition hover:border-acc/50"
                    >
                      <div className="flex items-start justify-between gap-4">
                        <div className="grid flex-1 grid-cols-1 gap-3 sm:grid-cols-3">
                          <div>
                            <MiniLabel>Item / Property Name</MiniLabel>
                            <MiniInput
                              value={item.name}
                              onChange={(e) => updateOffering(idx, 'name', e.target.value)}
                              className="mt-1"
                            />
                          </div>
                          <div>
                            <MiniLabel>Category / Type</MiniLabel>
                            <MiniInput
                              value={item.category}
                              onChange={(e) => updateOffering(idx, 'category', e.target.value)}
                              className="mt-1"
                            />
                          </div>
                          <div>
                            <MiniLabel>Price / Pricing Range</MiniLabel>
                            <MiniInput
                              value={item.price}
                              onChange={(e) => updateOffering(idx, 'price', e.target.value)}
                              className="mt-1"
                            />
                          </div>
                        </div>
                        <button
                          type="button"
                          onClick={() => removeOffering(idx)}
                          className="mt-5 rounded p-1.5 text-txmuted transition hover:bg-badbg hover:text-badink"
                          title="Remove item"
                        >
                          <Trash2 className="h-4 w-4" />
                        </button>
                      </div>

                      {/* Attributes (BHK, sqft, location, amenities, rera) */}
                      {item.attrs && Object.keys(item.attrs).length > 0 && (
                        <div className="mt-3 grid grid-cols-2 gap-2 border-t border-linesoft pt-3 sm:grid-cols-4">
                          {Object.entries(item.attrs).map(([k, val]) => (
                            <div key={k} className="text-xs">
                              <span className="text-[10px] font-medium uppercase text-txmuted">
                                {k.replace('_', ' ')}:
                              </span>
                              <input
                                type="text"
                                value={val}
                                onChange={(e) => updateOfferingAttr(idx, k, e.target.value)}
                                className="mt-0.5 w-full rounded border border-line bg-inputbg px-2 py-1 text-xs text-tx outline-none focus:border-acc"
                              />
                            </div>
                          ))}
                        </div>
                      )}

                      <div className="mt-3">
                        <MiniLabel>Detailed Description (for Bot RAG Retrieval)</MiniLabel>
                        <MiniArea
                          rows={2}
                          value={item.description}
                          onChange={(e) => updateOffering(idx, 'description', e.target.value)}
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
                <h4 className="text-sm font-semibold text-tx">
                  WhatsApp Interactive List Navigation
                </h4>
                <p className="text-xs text-txmuted">
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
                      className="flex items-center gap-3 rounded-panel border border-line bg-paper p-3"
                    >
                      <div className="grid flex-1 grid-cols-1 gap-2 sm:grid-cols-4">
                        <div>
                          <div className="flex justify-between">
                            <MiniLabel>Title</MiniLabel>
                            <span
                              className={`text-[10px] ${
                                titleTooLong ? 'font-bold text-badink' : 'text-txmuted'
                              }`}
                            >
                              {item.title.length}/24
                            </span>
                          </div>
                          <MiniInput
                            value={item.title}
                            onChange={(e) => updateMenuItem(idx, 'title', e.target.value)}
                            className="mt-1"
                            invalid={titleTooLong}
                          />
                        </div>
                        <div className="sm:col-span-2">
                          <div className="flex justify-between">
                            <MiniLabel>Description</MiniLabel>
                            <span
                              className={`text-[10px] ${
                                descTooLong ? 'font-bold text-badink' : 'text-txmuted'
                              }`}
                            >
                              {item.description.length}/72
                            </span>
                          </div>
                          <MiniInput
                            value={item.description}
                            onChange={(e) => updateMenuItem(idx, 'description', e.target.value)}
                            className="mt-1"
                            invalid={descTooLong}
                          />
                        </div>
                        <div>
                          <MiniLabel>Action</MiniLabel>
                          <MiniInput
                            value={item.action}
                            onChange={(e) => updateMenuItem(idx, 'action', e.target.value)}
                            className="mt-1"
                          />
                        </div>
                      </div>
                      <button
                        type="button"
                        onClick={() => removeMenuItem(idx)}
                        className="rounded p-1.5 text-txmuted transition hover:bg-badbg hover:text-badink"
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
                <h4 className="text-sm font-semibold text-tx">
                  Extracted FAQs &amp; Knowledge Base
                </h4>
                <p className="text-xs text-txmuted">
                  These verified Q&amp;A pairs will be embedded into your tenant vector search index for accurate AI answers.
                </p>
              </div>

              <div className="space-y-3">
                {draft.faqs.map((faq, idx) => (
                  <div
                    key={idx}
                    className="space-y-2 rounded-panel border border-line bg-paper p-4"
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="flex-1">
                        <MiniLabel>Customer Question</MiniLabel>
                        <MiniInput
                          value={faq.question}
                          onChange={(e) => updateFaq(idx, 'question', e.target.value)}
                          className="mt-1 font-medium"
                        />
                      </div>
                      <div className="w-36">
                        <MiniLabel>Category</MiniLabel>
                        <MiniInput
                          value={faq.category}
                          onChange={(e) => updateFaq(idx, 'category', e.target.value)}
                          className="mt-1"
                        />
                      </div>
                      <button
                        type="button"
                        onClick={() => removeFaq(idx)}
                        className="mt-5 rounded p-1.5 text-txmuted transition hover:bg-badbg hover:text-badink"
                      >
                        <Trash2 className="h-4 w-4" />
                      </button>
                    </div>
                    <div>
                      <MiniLabel>Verified Official Answer</MiniLabel>
                      <MiniArea
                        rows={2}
                        value={faq.answer}
                        onChange={(e) => updateFaq(idx, 'answer', e.target.value)}
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
                <h4 className="text-sm font-semibold text-tx">Intents for Intents Editor</h4>
                <p className="text-xs text-txmuted">
                  These answers publish into the tenant draft (technologies, careers, service_enquiry, pricing, etc.).
                </p>
              </div>
              {(draft.intents || []).length === 0 ? (
                <div className="rounded-panel border border-dashed border-line p-8 text-center text-xs text-txmuted">
                  No intents extracted. Catalog-linked service/pricing intents are added on publish when offerings exist.
                </div>
              ) : (
                <div className="space-y-3">
                  {(draft.intents || []).map((intent, idx) => (
                    <div
                      key={`${intent.name}-${idx}`}
                      className="space-y-2 rounded-panel border border-line bg-paper p-4"
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div className="grid flex-1 grid-cols-1 gap-3 sm:grid-cols-2">
                          <div>
                            <MiniLabel>Intent name</MiniLabel>
                            <MiniInput
                              value={intent.name}
                              onChange={(e) => updateIntent(idx, 'name', e.target.value)}
                              className="mt-1 font-medium"
                            />
                          </div>
                          <div>
                            <MiniLabel>Keywords (comma-separated)</MiniLabel>
                            <MiniInput
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
                              className="mt-1"
                            />
                          </div>
                        </div>
                        <button
                          type="button"
                          onClick={() => removeIntent(idx)}
                          className="mt-5 rounded p-1.5 text-txmuted transition hover:bg-badbg hover:text-badink"
                        >
                          <Trash2 className="h-4 w-4" />
                        </button>
                      </div>
                      <div>
                        <MiniLabel>Bot answer</MiniLabel>
                        <MiniArea
                          rows={4}
                          value={intent.answer || ''}
                          onChange={(e) => updateIntent(idx, 'answer', e.target.value)}
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
                <span className="text-xs font-semibold text-tx">Company Brand Name</span>
                <MiniInput
                  value={draft.brand.name}
                  onChange={(e) => patchBrand('name', e.target.value)}
                  className="mt-1"
                />
              </div>
              <div>
                <span className="text-xs font-semibold text-tx">AI Bot Assistant Name</span>
                <MiniInput
                  value={draft.brand.bot_name}
                  onChange={(e) => patchBrand('bot_name', e.target.value)}
                  className="mt-1"
                />
              </div>
              <div className="sm:col-span-2">
                <span className="text-xs font-semibold text-tx">Brand Tagline / Value Proposition</span>
                <MiniInput
                  value={draft.brand.tagline}
                  onChange={(e) => patchBrand('tagline', e.target.value)}
                  className="mt-1"
                />
              </div>
              <div>
                <span className="text-xs font-semibold text-tx">Support Email</span>
                <MiniInput
                  value={draft.brand.support_email}
                  onChange={(e) => patchBrand('support_email', e.target.value)}
                  className="mt-1"
                />
              </div>
              <div>
                <span className="text-xs font-semibold text-tx">Support Helpline Phone</span>
                <MiniInput
                  value={draft.brand.support_phone}
                  onChange={(e) => patchBrand('support_phone', e.target.value)}
                  className="mt-1"
                />
              </div>
              <div>
                <span className="text-xs font-semibold text-tx">Official Website</span>
                <MiniInput
                  value={draft.brand.website}
                  onChange={(e) => patchBrand('website', e.target.value)}
                  className="mt-1"
                />
              </div>
              <div>
                <span className="text-xs font-semibold text-tx">Working Hours</span>
                <MiniInput
                  value={draft.business_hours.raw || '09:30 AM - 06:30 PM (Mon-Sat)'}
                  onChange={(e) =>
                    setDraft((prev) => ({
                      ...prev,
                      business_hours: { ...prev.business_hours, raw: e.target.value },
                    }))
                  }
                  className="mt-1"
                />
              </div>
            </div>
          )}

          {/* TAB 5: GUARDRAILS */}
          {activeTab === 'guardrails' && (
            <div className="space-y-4">
              <div>
                <span className="text-xs font-semibold text-tx">Bot Tone &amp; Persona</span>
                <MiniInput
                  value={draft.guardrails.tone}
                  onChange={(e) =>
                    setDraft((prev) => ({
                      ...prev,
                      guardrails: { ...prev.guardrails, tone: e.target.value },
                    }))
                  }
                  className="mt-1"
                />
              </div>
              <div>
                <span className="text-xs font-semibold text-tx">
                  Strict Guardrails (What the bot must NEVER do)
                </span>
                <div className="mt-2 space-y-2">
                  {draft.guardrails.never_state.map((rule, rIdx) => (
                    <div key={rIdx} className="flex items-center gap-2">
                      <span className="text-xs font-bold text-badink">•</span>
                      <MiniInput
                        value={rule}
                        onChange={(e) => {
                          const updated = [...draft.guardrails.never_state]
                          updated[rIdx] = e.target.value
                          setDraft((prev) => ({
                            ...prev,
                            guardrails: { ...prev.guardrails, never_state: updated },
                          }))
                        }}
                        className="flex-1"
                      />
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between border-t border-line bg-card px-6 py-4">
          <GhostButton onClick={onClose} disabled={publishing}>
            Cancel / Discard
          </GhostButton>
          <div className="flex items-center gap-3">
            <span className="text-xs text-txmuted">
              {draft.offerings.length} items • {draft.faqs.length} FAQs ready to publish
            </span>
            <PrimaryButton disabled={publishing} onClick={handleConfirm}>
              {publishing ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Rocket className="h-4 w-4" />
              )}
              Confirm &amp; Publish to Live Bot
            </PrimaryButton>
          </div>
        </div>
    </Modal>
  )
}
