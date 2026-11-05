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
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Card, CardBody } from '@/components/ui/card'
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
  const [parsing, setParsing] = useState(false)
  const [parsingLane, setParsingLane] = useState<UploadLane | null>(null)
  const [stagedDraft, setStagedDraft] = useState<StagedOnboardingDraft | null>(null)
  const [publishing, setPublishing] = useState(false)
  const [showDropdown, setShowDropdown] = useState(false)

  useEffect(() => {
    let active = true
    setLoadingTemplates(true)
    onboardingKitsApi
      .list()
      .then((res) => {
        if (active && res.templates) setTemplates(res.templates)
      })
      .catch((err) => {
        console.warn('Failed loading onboarding kits:', err)
      })
      .finally(() => {
        if (active) setLoadingTemplates(false)
      })
    return () => {
      active = false
    }
  }, [])

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
        <Card className="border-sky-500/30 bg-sky-500/[0.02]">
          <CardBody className="p-4 sm:p-5">
            <div className="flex flex-col lg:flex-row items-start lg:items-center justify-between gap-4">
              <div className="space-y-2 flex-1">
                <div className="flex items-center gap-2">
                  <span className="flex h-7 w-7 items-center justify-center rounded-md bg-sky-500/10 text-sky-600">
                    <Files className="h-4 w-4" />
                  </span>
                  <h3 className="text-sm font-semibold text-surface-fg">Option A — Any document</h3>
                  <Badge variant="outline" className="border-sky-500/40 text-sky-700 bg-sky-500/10">
                    Universal extract
                  </Badge>
                </div>
                <p className="text-xs text-surface-muted max-w-xl">
                  Website scrape, PDF, Word, Markdown, or CSV. Fills brand, menu, offerings, FAQs, intents and KB for
                  this tenant — no kit template required.
                </p>
              </div>
              <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-sky-500/40 bg-sky-500/5 p-4 text-center sm:min-w-[240px]">
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
                <div className="flex h-10 w-10 items-center justify-center rounded-full bg-sky-500/15 text-sky-600 mb-2">
                  {parsing && parsingLane === 'any' ? (
                    <Loader2 className="h-5 w-5 animate-spin" />
                  ) : (
                    <FolderUp className="h-5 w-5" />
                  )}
                </div>
                <p className="text-xs font-medium text-surface-fg">
                  {parsing && parsingLane === 'any' ? 'Extracting…' : 'Upload any company file'}
                </p>
                <Button
                  variant="primary"
                  size="sm"
                  loading={parsing && parsingLane === 'any'}
                  disabled={parsing}
                  onClick={() => anyFileRef.current?.click()}
                  className="mt-2 text-xs"
                >
                  Upload & stage
                </Button>
              </div>
            </div>
          </CardBody>
        </Card>
        )}

        {/* Option B — filled industry kit */}
        <Card className="border-teal-500/30 bg-teal-500/[0.02]">
          <CardBody className="p-4 sm:p-5">
            <div className="flex flex-col lg:flex-row items-start lg:items-center justify-between gap-4">
              <div className="space-y-2 flex-1">
                <div className="flex items-center gap-2">
                  <span className="flex h-7 w-7 items-center justify-center rounded-md bg-teal-500/10 text-teal-600">
                    <Sparkles className="h-4 w-4" />
                  </span>
                  <h3 className="text-sm font-semibold text-surface-fg">Option B — Filled industry kit</h3>
                  <Badge variant="outline" className="border-teal-500/40 text-teal-700 bg-teal-500/10">
                    Structured sections
                  </Badge>
                </div>
                <p className="text-xs text-surface-muted max-w-xl">
                  Download a template, keep <code className="text-2xs">[SECTION: …]</code> headers (Brand, WhatsApp,
                  Catalog, FAQs, Bot Persona). Incomplete kits fall back to general extraction with a warning.
                </p>

                <div className="flex flex-wrap items-center gap-2 pt-1">
                  <div className="relative">
                    <button
                      type="button"
                      onClick={() => setShowDropdown(!showDropdown)}
                      disabled={loadingTemplates}
                      className="flex items-center gap-2 rounded-lg border border-surface-line bg-surface-card px-3 py-1.5 text-xs font-medium text-surface-fg hover:border-teal-500/50 transition"
                    >
                      <span>{currentTemplate?.icon || '🏢'}</span>
                      <span>{currentTemplate?.title || 'Select Industry'}</span>
                      <ChevronDown className="h-3 w-3 text-surface-muted" />
                    </button>
                    {showDropdown && (
                      <div className="absolute left-0 top-full z-30 mt-1 w-64 rounded-lg border border-surface-line bg-surface-card p-1 shadow-xl">
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
                                ? 'bg-teal-500/15 text-teal-700 font-medium'
                                : 'text-surface-fg hover:bg-surface-elevated'
                            }`}
                          >
                            <span>{tpl.icon}</span>
                            <span className="truncate">{tpl.title}</span>
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => handleDownload('docx')}
                    icon={<Download className="h-3.5 w-3.5 text-blue-600" />}
                  >
                    Word (.docx)
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => handleDownload('pdf')}
                    icon={<FileText className="h-3.5 w-3.5 text-rose-600" />}
                  >
                    PDF
                  </Button>
                </div>
              </div>

              <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-teal-500/40 bg-teal-500/5 p-4 text-center sm:min-w-[240px]">
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
                <div className="flex h-10 w-10 items-center justify-center rounded-full bg-teal-500/15 text-teal-600 mb-2">
                  {parsing && parsingLane === 'kit' ? (
                    <Loader2 className="h-5 w-5 animate-spin" />
                  ) : (
                    <FolderUp className="h-5 w-5" />
                  )}
                </div>
                <p className="text-xs font-medium text-surface-fg">
                  {parsing && parsingLane === 'kit' ? 'Parsing kit…' : 'Upload filled kit'}
                </p>
                <p className="text-[10px] text-surface-muted mt-0.5">Requires all 5 core sections</p>
                <Button
                  variant="primary"
                  size="sm"
                  loading={parsing && parsingLane === 'kit'}
                  disabled={parsing}
                  onClick={() => kitFileRef.current?.click()}
                  className="mt-2 text-xs"
                >
                  Upload kit & stage
                </Button>
              </div>
            </div>
          </CardBody>
        </Card>
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
