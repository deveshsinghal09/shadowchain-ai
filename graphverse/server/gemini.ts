import { z } from 'zod';
import type { Answer } from '../shared/types';
import { HttpError } from './repository';

const note = z
  .object({
    summary: z.string().min(1).max(1500),
    evidence_ids: z.array(z.string()).min(1).max(20),
  })
  .strict();

// Gemini can explain bounded graph facts only. It cannot supply paths, queries,
// commands or decisions. Raw user questions, labels and source documents are omitted.
export class GeminiExplainer {
  private lastFailure: string | null = null;
  constructor(
    private readonly key: string,
    private readonly model: string,
    private readonly request: typeof fetch = fetch,
  ) {}
  async readiness() {
    try {
      const response = await this.request(
        'https://generativelanguage.googleapis.com/v1beta/models?pageSize=100',
        {
          method: 'GET',
          redirect: 'error',
          signal: AbortSignal.timeout(8000),
          headers: { 'x-goog-api-key': this.key },
        },
      );
      if (!response.ok) {
        await response.body?.cancel();
        return {
          configured: true,
          model: this.model,
          model_list_http_status: response.status,
          last_generation_failure: this.lastFailure,
          available_models: [],
          generation_verified: false,
        };
      }
      const reader = response.body?.getReader();
      if (!reader) throw new Error('Missing provider metadata');
      const chunks: Uint8Array[] = [];
      let size = 0;
      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          size += value.length;
          if (size > 256000) throw new Error('Provider metadata limit');
          chunks.push(value);
        }
      } finally {
        await reader.cancel();
      }
      const data = JSON.parse(Buffer.concat(chunks).toString('utf8'));
      const models = z
        .array(
          z.object({
            name: z.string().regex(/^models\/[A-Za-z0-9.-]{1,100}$/),
            supportedGenerationMethods: z.array(z.string()).optional(),
          }),
        )
        .max(100)
        .parse(data.models);
      return {
        configured: true,
        model: this.model,
        model_list_http_status: 200,
        last_generation_failure: this.lastFailure,
        available_models: models
          .filter((m) => m.supportedGenerationMethods?.includes('generateContent'))
          .map((m) => m.name.slice(7)),
        generation_verified: false,
      };
    } catch {
      return {
        configured: true,
        model: this.model,
        model_list_http_status: null,
        last_generation_failure: this.lastFailure,
        available_models: [],
        generation_verified: false,
      };
    }
  }

  async explain(answer: Answer): Promise<Answer> {
    if (!answer.evidence.length) return answer;
    this.lastFailure = null;
    // Use temporary opaque citations so even imported IDs never reach the provider.
    const citations = answer.evidence.slice(0, 20).map((_, index) => `evidence-${index + 1}`);
    const facts = {
      entity_count: answer.entities.length,
      relationship_count: answer.evidence.length,
      bounded_path_found: answer.path?.found ?? null,
      evidence_ids: citations,
      effective_authorization_verified: false,
    };
    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        const response = await this.request(
          `https://generativelanguage.googleapis.com/v1beta/models/${this.model}:generateContent`,
          {
            method: 'POST',
            redirect: 'error',
            signal: AbortSignal.timeout(10000),
            headers: { 'Content-Type': 'application/json', 'x-goog-api-key': this.key },
            body: JSON.stringify({
              systemInstruction: {
                parts: [
                  {
                    text: 'Summarize only these graph-derived counts. Cite supplied evidence IDs. A relationship path is not proof of effective authorization. Do not infer exploitability, propose commands or request tools.',
                  },
                ],
              },
              contents: [{ role: 'user', parts: [{ text: JSON.stringify(facts) }] }],
              generationConfig: {
                temperature: 0,
                maxOutputTokens: 768,
                ...(this.model === 'gemini-2.5-flash'
                  ? { thinkingConfig: { thinkingBudget: 0 } }
                  : {}),
                ...(this.model === 'gemini-3.1-flash-lite'
                  ? { thinkingConfig: { thinkingLevel: 'minimal', includeThoughts: false } }
                  : {}),
                responseMimeType: 'application/json',
                responseJsonSchema: z.toJSONSchema(note),
              },
            }),
          },
        );
        if (!response.ok) {
          this.lastFailure = 'http_' + response.status;
          await response.body?.cancel();
          if (response.status !== 429 && response.status < 500) break;
          continue;
        }
        const reader = response.body?.getReader();
        if (!reader) throw new Error('Missing model response');
        let size = 0;
        const chunks: Uint8Array[] = [];
        try {
          while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            size += value.length;
            if (size > 32768) throw new Error('Model response limit');
            chunks.push(value);
          }
        } finally {
          await reader.cancel();
        }
        const envelope = JSON.parse(Buffer.concat(chunks).toString('utf8'));
        this.lastFailure = 'invalid_or_incomplete_output';
        const candidates = envelope.candidates;
        if (candidates?.length !== 1 || candidates[0].finishReason !== 'STOP') {
          this.lastFailure = 'incomplete_candidate';
          throw new Error('Incomplete model response');
        }
        const parts = candidates[0].content.parts;
        if (parts?.length !== 1) throw new Error('Unexpected model action');
        // Gemini 3 may attach an opaque thoughtSignature to a normal text part.
        // It is transport metadata, not a tool or a summary; discard it here.
        this.lastFailure = 'invalid_response_part';
        const part = z
          .object({
            text: z.string().max(16000),
            thoughtSignature: z.string().max(24000).optional(),
            thought: z.literal(false).optional(),
          })
          .strict()
          .parse(parts[0]);
        this.lastFailure = 'invalid_summary_schema';
        const result = note.parse(JSON.parse(part.text));
        if (result.evidence_ids.some((id) => !citations.includes(id)))
          throw new Error('Ungrounded citation');
        // Retain the authoritative explanation and exact path unchanged.
        this.lastFailure = null;
        return { ...answer, mode: 'gemini', model_note: result.summary };
      } catch {
        this.lastFailure ??= 'transport_timeout_or_error';
        /* Never expose model response bodies, prompts, keys or transport URLs. */
      }
    }
    throw new HttpError(
      503,
      'model_unavailable',
      'Gemini did not return a valid explanation. Graph browsing and path queries remain available.',
    );
  }
}
