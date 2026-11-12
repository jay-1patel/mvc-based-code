import { useEffect, useRef, useState } from 'react'
import {
  Download,
  FileText,
  FolderUp,
  Loader2,
  Sparkles,
  ChevronDown,
  Files,
} from 'lucide-react'
import {
  onboardingKitsApi,
  type OnboardingTemplate,
  type StagedOnboardingDraft,
} from '@/lib/uploads'
import { GhostButton, Notice, PrimaryButton } from '@/components/remake/ui'
import { useToast } from '@/components/ui/toast'
import { OnboardingReviewModal } from './onboarding-review-modal'

interface OnboardingKitWidgetProps {
  tenantId?: string
  onStagedDraft?: (draft: StagedOnboardingDraft) => void
  onPublished?: (summary: any) => void
  compact?: boolean
  /** Hide the "Option A — Any document" lane; kit upload only. */
  kitOnly?: boolean
}

type UploadLane = 'any' | 'kit'

export function OnboardingKitWidget({
  tenantId,
  onStagedDraft,
  onPublished,
  kitOnly,
}: OnboardingKitWidgetProps) {
  const toast = useToast()
  const anyFileRef = useRef<HTMLInputElement>(null)
  const kitFileRef = useRef<HTMLInputElement>(null)

  const [templates, setTemplates] = useState<OnboardingTemplate[]>([])
  const [selectedVertical, setSelectedVertical] = useState<string>('real_estate')
  const [loadingTemplates, setLoadingTemplates] = useState(false)
  const [templatesError, setTemplatesError] = useState<string | null>(null)
  const [templatesAttempt, setTemplatesAttempt] = useState(0)
  const [parsing, setParsing] = useState(false)
  const [parsingLane, setParsingLane] = useState<UploadLane | null>(null)
  const [stagedDraft, setStagedDraft] = useState<StagedOnboardingDraft | null>(null)
  const [publishing, setPublishing] = useState(false)
  const [showDropdown, setShowDropdown] = useState(false)

  useEffect(() => {
    let active = true
    setLoadingTemplates(true)
    setTemplatesError(null)
    onboardingKitsApi
      .list()
      .then((res) => {
        if (active && res.templates) setTemplates(res.templates)
      })
      .catch((err) => {
        console.warn('Failed loading onboarding kits:', err)
        if (active) {
          setTemplatesError(
            err instanceof Error ? err.message : 'The industry kit list could not be loaded.',
          )
        }
      })
      .finally(() => {
        if (active) setLoadingTemplates(false)
      })
    return () => {
      active = false
    }
  }, [templatesAttempt])

  const currentTemplate = templates.find((t) => t.vertical === selectedVertical) || templates[0]

  const handleDownload = (format: 'docx' | 'pdf') => {
    const slug = currentTemplate?.vertical || selectedVertical
    const url = `/api/admin/onboarding-kits/${slug}/${format}`
    window.open(url, '_blank')
    toast.push(`Downloading ${currentTemplate?.title || slug} ${format.toUpperCase()} template...`)
  }

  const handleFileUpload = async (file: File, lane: UploadLane) => {
    setParsing(true)
    setParsingLane(lane)
    try {
      toast.push(
        lane === 'kit'
          ? `Staging kit ${file.name}…`
          : `Extracting bot details from ${file.name}…`,
      )
      let draft: StagedOnboardingDraft
      if (tenantId && tenantId !== 'draft_tenant') {
        draft = await onboardingKitsApi.stageTenant(tenantId, file, lane)
      } else {
        draft = await onboardingKitsApi.stageGlobal(file, lane)
      }
      setStagedDraft(draft)
      if (onStagedDraft) onStagedDraft(draft)

      if (draft.warning) {
        toast.push(draft.warning, 'error')
      }
      const mode = draft.extraction_mode === 'kit' ? 'kit parse' : 'general extraction'
      toast.push(
        `Ready to review (${mode}): ${draft.offerings?.length || 0} offerings, ${draft.faqs?.length || 0} FAQs, ${draft.intents?.length || 0} intents`,
      )
    } catch (err: any) {
      console.error('Document parsing failed:', err)
      toast.push(`Parsing failed: ${err.message || 'Check document format and try again'}`, 'error')
    } finally {
      setParsing(false)
      setParsingLane(null)
      if (anyFileRef.current) anyFileRef.current.value = ''
      if (kitFileRef.current) kitFileRef.current.value = ''
    }
  }

  const handleConfirmPublish = async (finalDraft: StagedOnboardingDraft) => {
    const tid = tenantId || finalDraft.tenant_id
    if (!tid || tid === 'draft_tenant') {
      if (onStagedDraft) onStagedDraft(finalDraft)
      setStagedDraft(null)
      toast.push('Full staged configuration saved to wizard!')
      return
    }

    setPublishing(true)
    try {
      const res = await onboardingKitsApi.publishTenant(tid, finalDraft)
      const intentsN = res.summary?.intents_count ?? finalDraft.intents?.length ?? 0
      toast.push(
        `Published! ${res.summary?.offerings_created ?? 0} offerings, ${res.summary?.kb_chunks_indexed ?? 0} KB chunks, ${intentsN} intents — Intents Editor updated.`,
      )
      setStagedDraft(null)
      if (onPublished) onPublished(res.summary)
    } catch (err: any) {
      toast.push(`Publishing failed: ${err.message || 'Server error'}`, 'error')
    } finally {
      setPublishing(false)
    }
  }

  return (
    <>
      <div className="space-y-4">
        {/* Option A — any document */}
        {!kitOnly && (
        <div className="rounded-panel border border-chipwebink/30 bg-chipwebbg/40 p-4 sm:p-5">
            <div className="flex flex-col items-start justify-between gap-4 lg:flex-row lg:items-center">
              <div className="flex-1 space-y-2">
                <div className="flex items-center gap-2">
                  <span className="flex h-7 w-7 items-center justify-center rounded-md bg-chipwebbg text-chipwebink">
                    <Files className="h-4 w-4" />
                  </span>
                  <h3 className="text-sm font-semibold text-tx">Option A — Any document</h3>
                  <span className="inline-flex rounded-full border border-chipwebink/30 bg-chipwebbg px-2 py-0.5 text-2xs font-medium text-chipwebink">
                    Universal extract
                  </span>
                </div>
                <p className="max-w-xl text-xs leading-relaxed text-txmuted">
                  Website scrape, PDF, Word, Markdown, or CSV. Fills brand, menu, offerings, FAQs, intents and KB for
                  this tenant — no kit template required.
                </p>
              </div>
              <div className="flex flex-col items-center justify-center rounded-panel border border-dashed border-chipwebink/40 bg-chipwebbg/40 p-4 text-center sm:min-w-[240px]">
                <input
                  ref={anyFileRef}
                  type="file"
                  accept=".docx,.pdf,.doc,.xlsx,.csv,.txt,.md"
                  className="hidden"
                  onChange={(e) => {
                    const f = e.target.files?.[0]
                    if (f) handleFileUpload(f, 'any')
                  }}
                />
                <div className="mb-2 flex h-10 w-10 items-center justify-center rounded-full bg-chipwebbg text-chipwebink">
                  {parsing && parsingLane === 'any' ? (
                    <Loader2 className="h-5 w-5 animate-spin" />
                  ) : (
                    <FolderUp className="h-5 w-5" />
                  )}
                </div>
                <p className="text-xs font-medium text-tx">
                  {parsing && parsingLane === 'any' ? 'Extracting…' : 'Upload any company file'}
                </p>
                <PrimaryButton
                  className="mt-2 px-2.5 py-1.5 text-xs"
                  disabled={parsing}
                  onClick={() => anyFileRef.current?.click()}
                >
                  {parsing && parsingLane === 'any' && (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  )}
                  Upload &amp; stage
                </PrimaryButton>
              </div>
            </div>
        </div>
        )}

        {/* Option B — filled industry kit */}
        <div className="rounded-panel border border-acc/30 bg-accsoft/40 p-4 sm:p-5">
            <div className="flex flex-col items-start justify-between gap-4 lg:flex-row lg:items-center">
              <div className="flex-1 space-y-2">
                <div className="flex items-center gap-2">
                  <span className="flex h-7 w-7 items-center justify-center rounded-md bg-accsoft text-acc">
                    <Sparkles className="h-4 w-4" />
                  </span>
                  <h3 className="text-sm font-semibold text-tx">Option B — Filled industry kit</h3>
                  <span className="inline-flex rounded-full border border-acc/40 bg-accsoft px-2 py-0.5 text-2xs font-medium text-acc">
                    Structured sections
                  </span>
                </div>
                <p className="max-w-xl text-xs leading-relaxed text-txmuted">
                  Download a template, keep <code className="text-2xs">[SECTION: …]</code> headers (Brand, WhatsApp,
                  Catalog, FAQs, Bot Persona). Incomplete kits fall back to general extraction with a warning.
                </p>

                {templatesError && (
                  <Notice tone="bad" title="Industry kits failed to load" className="max-w-xl">
                    {templatesError}
                    <div className="mt-2">
                      <GhostButton
                        className="px-2.5 py-1.5 text-xs"
                        disabled={loadingTemplates}
                        onClick={() => setTemplatesAttempt((n) => n + 1)}
                      >
                        {loadingTemplates && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                        Retry
                      </GhostButton>
                    </div>
                  </Notice>
                )}

                <div className="flex flex-wrap items-center gap-2 pt-1">
                  <div className="relative">
                    <button
                      type="button"
                      onClick={() => setShowDropdown(!showDropdown)}
                      disabled={loadingTemplates}
                      className="flex items-center gap-2 rounded-lg border border-line bg-card px-3 py-1.5 text-xs font-medium text-tx transition hover:border-acc"
                    >
                      <span>{currentTemplate?.icon || '🏢'}</span>
                      <span>{currentTemplate?.title || 'Select Industry'}</span>
                      <ChevronDown className="h-3 w-3 text-txmuted" />
                    </button>
                    {showDropdown && (
                      <div className="absolute left-0 top-full z-30 mt-1 w-64 rounded-lg border border-line bg-card p-1 shadow-xl">
                        {templates.map((tpl) => (
                          <button
                            key={tpl.vertical}
                            type="button"
                            onClick={() => {
                              setSelectedVertical(tpl.vertical)
                              setShowDropdown(false)
                            }}
                            className={`flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-xs transition ${
                              selectedVertical === tpl.vertical
                                ? 'bg-accsoft font-medium text-acc'
                                : 'text-tx hover:bg-paper'
                            }`}
                          >
                            <span>{tpl.icon}</span>
                            <span className="truncate">{tpl.title}</span>
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                  <GhostButton
                    className="px-2.5 py-1.5 text-xs"
                    onClick={() => handleDownload('docx')}
                  >
                    <Download className="h-3.5 w-3.5 text-acc" />
                    Word (.docx)
                  </GhostButton>
                  <GhostButton
                    className="px-2.5 py-1.5 text-xs"
                    onClick={() => handleDownload('pdf')}
                  >
                    <FileText className="h-3.5 w-3.5 text-badink" />
                    PDF
                  </GhostButton>
                </div>
              </div>

              <div className="flex flex-col items-center justify-center rounded-panel border border-dashed border-acc/40 bg-accsoft/40 p-4 text-center sm:min-w-[240px]">
                <input
                  ref={kitFileRef}
                  type="file"
                  accept=".docx,.pdf,.doc"
                  className="hidden"
                  onChange={(e) => {
                    const f = e.target.files?.[0]
                    if (f) handleFileUpload(f, 'kit')
                  }}
                />
                <div className="mb-2 flex h-10 w-10 items-center justify-center rounded-full bg-accsoft text-acc">
                  {parsing && parsingLane === 'kit' ? (
                    <Loader2 className="h-5 w-5 animate-spin" />
                  ) : (
                    <FolderUp className="h-5 w-5" />
                  )}
                </div>
                <p className="text-xs font-medium text-tx">
                  {parsing && parsingLane === 'kit' ? 'Parsing kit…' : 'Upload filled kit'}
                </p>
                <p className="mt-0.5 text-[10px] text-txmuted">Requires all 5 core sections</p>
                <PrimaryButton
                  className="mt-2 px-2.5 py-1.5 text-xs"
                  disabled={parsing}
                  onClick={() => kitFileRef.current?.click()}
                >
                  {parsing && parsingLane === 'kit' && (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  )}
                  Upload kit &amp; stage
                </PrimaryButton>
              </div>
            </div>
        </div>
      </div>

      {stagedDraft && (
        <OnboardingReviewModal
          draft={stagedDraft}
          onClose={() => setStagedDraft(null)}
          onConfirm={handleConfirmPublish}
          publishing={publishing}
        />
      )}
    </>
  )
}
