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
  constructor(
    private readonly key: string,
    private readonly model: string,
    private readonly request: typeof fetch = fetch,
  ) {}

  async explain(answer: Answer): Promise<Answer> {
    if (!answer.evidence.length) return answer;
    // Use temporary opaque citations so even imported IDs never reach the provider.
    const citations = answer.evidence.slice(0, 20).map((_, index) => `evidence-${index + 1}`);
    const facts = {
      entity_count: answer.entities.length,
      relationship_count: answer.evidence.length,
      bounded_path_found: answer.path?.found ?? null,
      evidence_ids: citations,
      effective_aws_authorization_verified: false,
    };
    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        const response = await this.request(
          `https://generativelanguage.googleapis.com/v1beta/models/${this.model}:generateContent`,
          {
            method: 'POST',
            redirect: 'error',
            signal: AbortSignal.timeout(4000),
            headers: { 'Content-Type': 'application/json', 'x-goog-api-key': this.key },
            body: JSON.stringify({
              systemInstruction: {
                parts: [
                  {
                    text: 'Summarize only these graph-derived counts. Cite supplied evidence IDs. A relationship path is not proof of effective AWS access. Do not infer exploitability, propose commands or request tools.',
                  },
                ],
              },
              contents: [{ role: 'user', parts: [{ text: JSON.stringify(facts) }] }],
              generationConfig: {
                temperature: 0,
                maxOutputTokens: 512,
                responseMimeType: 'application/json',
                responseJsonSchema: z.toJSONSchema(note),
              },
            }),
          },
        );
        if (!response.ok) {
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
        const candidates = envelope.candidates;
        if (candidates?.length !== 1 || candidates[0].finishReason !== 'STOP')
          throw new Error('Incomplete model response');
        const parts = candidates[0].content.parts;
        if (parts?.length !== 1 || Object.keys(parts[0]).some((key) => key !== 'text'))
          throw new Error('Unexpected model action');
        const result = note.parse(JSON.parse(parts[0].text));
        if (result.evidence_ids.some((id) => !citations.includes(id)))
          throw new Error('Ungrounded citation');
        // Retain the authoritative explanation and exact path unchanged.
        return { ...answer, mode: 'gemini', model_note: result.summary };
      } catch {
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
