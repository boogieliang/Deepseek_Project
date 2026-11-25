/**
 * Minimal, host-independent client for the Agnes Image generation API.
 *
 * This module deliberately imports nothing but Node built-ins so it can be
 * unit-tested and exercised by a plain `node` process. The harness-specific
 * wiring lives in `index.js`.
 */

/** Default API surface documented for agnes-image-2.5-flash. */
export const AGNES_DEFAULT_BASE_URL = 'https://apihub.agnes-ai.com/v1'
/** Default image model. */
export const AGNES_DEFAULT_MODEL = 'agnes-image-2.5-flash'
/** Supported resolution tiers. */
export const AGNES_SIZES = ['1K', '2K', '3K', '4K']
/** Supported aspect ratios. */
export const AGNES_RATIOS = ['1:1', '3:4', '4:3', '16:9', '9:16', '2:3', '3:2', '21:9']

const DEFAULT_TIMEOUT_MS = 300_000
const MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024

/** Error raised for every non-success path: HTTP status, API message, or timeout. */
export class AgnesApiError extends Error {
  constructor(message, options = {}) {
    super(message, options)
    this.name = 'AgnesApiError'
    this.status = options.status
  }
}

/**
 * Build the JSON body for POST /v1/images/generations.
 *
 * The documentation is explicit that `response_format` must live under
 * `extra_body`, never at the top level, and that image-to-image inputs belong
 * in `extra_body.image` (there is no `tags: ["img2img"]`).
 */
export function buildGenerationRequest({
  model = AGNES_DEFAULT_MODEL,
  prompt,
  size = '2K',
  ratio = '1:1',
  images = [],
  responseFormat = 'url',
} = {}) {
  if (typeof prompt !== 'string' || prompt.trim().length === 0) {
    throw new AgnesApiError('prompt must be a non-empty string')
  }
  if (!AGNES_SIZES.includes(size)) {
    throw new AgnesApiError(`size must be one of ${AGNES_SIZES.join(', ')}`)
  }
  if (ratio !== undefined && !AGNES_RATIOS.includes(ratio)) {
    throw new AgnesApiError(`ratio must be one of ${AGNES_RATIOS.join(', ')}`)
  }
  if (!Array.isArray(images)) throw new AgnesApiError('images must be an array')

  const body = {
    model,
    prompt: prompt.trim(),
    size,
    ratio,
    extra_body: { response_format: responseFormat },
  }
  if (images.length > 0) body.extra_body.image = [...images]
  return body
}

/** Extract `{ b64Json, url }` from a successful response payload. */
export function parseGenerationResponse(json) {
  const first = Array.isArray(json?.data) ? json.data[0] : undefined
  if (first === undefined || first === null || typeof first !== 'object') {
    throw new AgnesApiError('Agnes API returned no image data')
  }
  const b64 = typeof first.b64_json === 'string' && first.b64_json.length > 0 ? first.b64_json : undefined
  const url = typeof first.url === 'string' && first.url.length > 0 ? first.url : undefined
  if (b64 === undefined && url === undefined) {
    throw new AgnesApiError('Agnes API returned neither url nor b64_json for the generated image')
  }
  return {
    b64Json: b64,
    url,
    taskId: typeof json?.task_id === 'string' ? json.task_id : undefined,
    revisedPrompt: typeof first.revised_prompt === 'string' ? first.revised_prompt : undefined,
  }
}

/** Read an error message out of an API error body without echoing the request. */
function describeApiError(status, text) {
  let detail = text?.trim() ?? ''
  try {
    const parsed = JSON.parse(text)
    const message = parsed?.error?.message ?? parsed?.message ?? parsed?.detail
    if (typeof message === 'string' && message.trim().length > 0) detail = message.trim()
  } catch {
    // Non-JSON error body: keep the raw text (bounded below).
  }
  if (detail.length > 600) detail = `${detail.slice(0, 600)}…`
  return `Agnes image API request failed with HTTP ${status}${detail === '' ? '' : `: ${detail}`}`
}

/** Client bound to one endpoint, model, and key resolver. */
export class AgnesImageClient {
  constructor({
    baseURL = AGNES_DEFAULT_BASE_URL,
    model = AGNES_DEFAULT_MODEL,
    timeoutMs = DEFAULT_TIMEOUT_MS,
    resolveApiKey,
    fetchImpl = globalThis.fetch,
  } = {}) {
    this.baseURL = baseURL.replace(/\/+$/u, '')
    this.model = model
    this.timeoutMs = timeoutMs
    this.resolveApiKey = resolveApiKey
    this.fetchImpl = fetchImpl
  }

  /** Compose the abort signal for one request: caller cancellation plus a hard timeout. */
  #signal(signal) {
    const timeout = AbortSignal.timeout(this.timeoutMs)
    return signal === undefined ? timeout : AbortSignal.any([signal, timeout])
  }

  /**
   * Generate one image and return its bytes plus the API metadata.
   * @returns {Promise<{ bytes: Buffer, url?: string, taskId?: string, revisedPrompt?: string, model: string }>}
   */
  async generate({ prompt, size, ratio, images = [], signal } = {}) {
    const apiKey = await this.resolveApiKey?.()
    if (typeof apiKey !== 'string' || apiKey.trim().length === 0) {
      throw new AgnesApiError('Agnes API key is not configured (expected credential reference AGNES_API_KEY)')
    }
    const body = buildGenerationRequest({ model: this.model, prompt, size, ratio, images })
    let response
    try {
      response = await this.fetchImpl(`${this.baseURL}/images/generations`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${apiKey.trim()}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(body),
        signal: this.#signal(signal),
      })
    } catch (error) {
      if (error?.name === 'TimeoutError' || error?.name === 'AbortError') {
        throw new AgnesApiError(`Agnes image generation timed out after ${Math.round(this.timeoutMs / 1000)}s`, { cause: error })
      }
      throw new AgnesApiError(`Agnes image API is unreachable: ${error?.message ?? String(error)}`, { cause: error })
    }

    const text = await response.text()
    if (!response.ok) throw new AgnesApiError(describeApiError(response.status, text), { status: response.status })

    let json
    try {
      json = JSON.parse(text)
    } catch (error) {
      throw new AgnesApiError('Agnes image API returned a non-JSON response', { cause: error })
    }
    const parsed = parseGenerationResponse(json)

    if (parsed.b64Json !== undefined) {
      const bytes = Buffer.from(parsed.b64Json, 'base64')
      if (bytes.byteLength === 0) throw new AgnesApiError('Agnes image API returned an empty base64 image')
      return { bytes, url: undefined, taskId: parsed.taskId, revisedPrompt: parsed.revisedPrompt, model: this.model }
    }

    const downloaded = await downloadImageBytes(parsed.url, { signal, timeoutMs: this.timeoutMs, fetchImpl: this.fetchImpl })
    return { bytes: downloaded.bytes, url: parsed.url, taskId: parsed.taskId, revisedPrompt: parsed.revisedPrompt, model: this.model }
  }
}

/** Download a generated image URL with a bounded size and timeout. */
export async function downloadImageBytes(
  url,
  { signal, timeoutMs = DEFAULT_TIMEOUT_MS, maxBytes = MAX_DOWNLOAD_BYTES, fetchImpl = globalThis.fetch } = {},
) {
  const timeout = AbortSignal.timeout(timeoutMs)
  let response
  try {
    response = await fetchImpl(url, {
      method: 'GET',
      redirect: 'follow',
      signal: signal === undefined ? timeout : AbortSignal.any([signal, timeout]),
    })
  } catch (error) {
    if (error?.name === 'TimeoutError' || error?.name === 'AbortError') {
      throw new AgnesApiError(`timed out downloading the generated image after ${Math.round(timeoutMs / 1000)}s`, { cause: error })
    }
    throw new AgnesApiError(`failed to download the generated image: ${error?.message ?? String(error)}`, { cause: error })
  }
  if (!response.ok) throw new AgnesApiError(`failed to download the generated image (HTTP ${response.status})`)
  const declared = Number(response.headers.get('content-length'))
  if (Number.isFinite(declared) && declared > maxBytes) {
    throw new AgnesApiError(`generated image is larger than the ${Math.round(maxBytes / (1024 * 1024))} MB download limit`)
  }
  const bytes = Buffer.from(await response.arrayBuffer())
  if (bytes.byteLength === 0) throw new AgnesApiError('downloaded generated image is empty')
  if (bytes.byteLength > maxBytes) {
    throw new AgnesApiError(`generated image is larger than the ${Math.round(maxBytes / (1024 * 1024))} MB download limit`)
  }
  return { bytes, contentType: response.headers.get('content-type') ?? undefined }
}
