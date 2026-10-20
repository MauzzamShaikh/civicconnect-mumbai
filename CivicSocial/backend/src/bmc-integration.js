import { createHash } from "node:crypto";
import { inferClearCivicComplaint, mentionsMyBmc } from "./civic-relevance.js";
import { BMC_CATEGORIES, classifyCivicComplaint } from "./groq-civic-classifier.js";

const DELIVERY_PATH = "/integrations/civicsocial/complaints";
const LOCK_MS = 2 * 60_000;
const RETRY_BASE_MS = 30_000;
const RETRY_MAX_MS = 60 * 60_000;
const IMAGE_MIME_TYPES = new Set(["image/jpeg", "image/png", "image/webp"]);
const VIDEO_MIME_TYPES = new Set(["video/mp4"]);
const DEFAULT_OPERATORS = { in: Symbol.for("in"), or: Symbol.for("or"), lte: Symbol.for("lte") };

function cleanBaseUrl(value, name, requireHttps = false) {
  if (!value) throw new Error(`${name} is required for BMC complaint delivery`);
  let parsed;
  try { parsed = new URL(value); } catch { throw new Error(`${name} must be a valid HTTP(S) URL`); }
  if (!new Set(["http:", "https:"]).has(parsed.protocol) || parsed.username || parsed.password || parsed.search || parsed.hash) {
    throw new Error(`${name} must be a valid HTTP(S) base URL without credentials, query, or fragment`);
  }
  if (requireHttps && parsed.protocol !== "https:") throw new Error(`${name} must use HTTPS in production`);
  return parsed.toString().replace(/\/$/, "");
}

function buildMediaPayload(value, kind) {
  if (!value) return { value: undefined, warning: null };
  const match = /^data:([^;,]+);base64,([a-z\d+/=\r\n]+)$/i.exec(String(value));
  const allowed = kind === "image" ? IMAGE_MIME_TYPES : VIDEO_MIME_TYPES;
  if (!match || !allowed.has(match[1].toLowerCase())) {
    return { value: undefined, warning: `Unsupported ${kind} format omitted from BMC request` };
  }
  const encoded = match[2].replace(/\s/g, "");
  if (!encoded || encoded.length % 4 === 1) return { value: undefined, warning: `Invalid ${kind} data omitted from BMC request` };
  return { value: `data:${match[1].toLowerCase()};base64,${encoded}`, warning: null };
}

function complaintUrl(frontendUrl, postId) {
  let url;
  try { url = new URL(frontendUrl); } catch { throw new Error("FRONTEND_URL must be valid to construct a CivicSocial post URL"); }
  if (!new Set(["http:", "https:"]).has(url.protocol)) throw new Error("FRONTEND_URL must use HTTP(S)");
  return `${url.origin}/#post/${encodeURIComponent(String(postId))}`;
}

function deliveryIdentity(post) {
  const postId = String(post.id);
  const timestamp = new Date(post.created_at ?? post.createdAt).getTime();
  if (!Number.isFinite(timestamp)) return postId;
  // Keep within the existing VARCHAR(32) outbox column without a migration.
  // The timestamp and text fingerprint prevent a reused sequence ID from
  // inheriting an unrelated earlier delivery state.
  const contentHash = createHash("sha256").update(String(post.text || "")).digest("hex").slice(0, 12);
  return `${postId}~${timestamp.toString(36)}~${contentHash}`.slice(0, 32);
}

function socialPostId(identity) {
  return String(identity).split("~", 1)[0];
}

function retryDelay(attemptCount) {
  return Math.min(RETRY_BASE_MS * (2 ** Math.max(0, attemptCount - 1)), RETRY_MAX_MS);
}

function errorMessage(error, secret) {
  let message = String(error?.message || "BMC delivery failed");
  const privateValue = String(secret || "").trim();
  if (privateValue) message = message.replaceAll(privateValue, "[redacted]");
  return message.slice(0, 1000);
}

export function createBmcIntegration({
  Delivery, User, Post, socialPlatform, config, fetchImpl = fetch, classifyCivicPost,
  now = () => new Date(), logger = console, operators = DEFAULT_OPERATORS,
}) {
  const classifyPost = classifyCivicPost || ((text) => classifyCivicComplaint(text, {
    apiKey: config.groqApiKey,
    model: config.groqModel,
  }));

  function isIntegrationCandidate(post) {
    const author = post?.author;
    return Boolean(author && !author.is_official
      && String(author.handle || "").toLowerCase() !== "mybmc"
      && mentionsMyBmc(post.text));
  }

  function outcome(delivery, classification = null, classificationStatus = "completed", reason = null) {
    return {
      status: delivery.status,
      complaintReference: delivery.complaintReference || null,
      lastError: delivery.lastError || null,
      mediaWarning: delivery.mediaWarning || null,
      attemptCount: delivery.attemptCount,
      classification: classification?.classification || null,
      category: classification?.category || "unknown",
      reason: reason ?? classification?.reason ?? null,
      classificationStatus,
    };
  }

  function getConfiguration() {
    const baseUrl = cleanBaseUrl(config.bmcApiBaseUrl, "BMC_API_BASE_URL", config.production);
    if (!config.bmcIntegrationSecret?.trim()) throw new Error("BMC_INTEGRATION_SECRET is required for BMC complaint delivery");
    const citizenUrl = cleanBaseUrl(config.bmcCitizenFrontendUrl, "BMC_CITIZEN_FRONTEND_URL", config.production);
    return { endpoint: `${baseUrl}${DELIVERY_PATH}`, citizenUrl };
  }

  async function createOrFind(post) {
    const originalPostId = deliveryIdentity(post);
    const [delivery] = await Delivery.findOrCreate({
      where: { originalPostId },
      defaults: { originalPostId, status: "pending", attemptCount: 0 },
    });
    return delivery;
  }

  async function claim(delivery) {
    const at = now();
    if (["acknowledged", "failed"].includes(delivery.status)) return null;
    if (["retry", "classification_retry", "registered"].includes(delivery.status) && delivery.nextAttemptAt && delivery.nextAttemptAt > at) return null;
    const ackPhase = ["registered", "acknowledging"].includes(delivery.status);
    const allowedStatuses = ackPhase ? ["registered", "acknowledging"] : ["pending", "retry", "classification_retry", "sending"];
    const [claimed] = await Delivery.update({
      status: ackPhase ? "acknowledging" : "sending",
      lockedUntil: new Date(at.getTime() + LOCK_MS),
    }, {
      where: {
        id: delivery.id,
        status: { [operators.in]: allowedStatuses },
        [operators.or]: [{ lockedUntil: null }, { lockedUntil: { [operators.lte]: at } }],
      },
    });
    return claimed ? (ackPhase ? "ack" : "send") : null;
  }

  async function markRetry(delivery, error, { retryable = true, incrementAttempt = false, delayMs } = {}) {
    const attemptCount = delivery.attemptCount + (incrementAttempt ? 1 : 0);
    const message = errorMessage(error, config.bmcIntegrationSecret);
    await delivery.update({
      status: retryable ? "retry" : "failed",
      attemptCount,
      nextAttemptAt: retryable ? new Date(now().getTime() + (delayMs || retryDelay(Math.max(attemptCount, 1)))) : null,
      lastError: message,
      lockedUntil: null,
    });
    const log = retryable ? logger.warn || logger.error : logger.error;
    log?.call(logger, `BMC delivery for CivicSocial post ${delivery.originalPostId} ${retryable ? "will retry" : "failed"}: ${message}`);
  }

  async function markClassificationRetry(delivery, error) {
    const attemptCount = delivery.attemptCount + 1;
    const message = errorMessage(error, config.groqApiKey);
    await delivery.update({
      status: "classification_retry",
      attemptCount,
      nextAttemptAt: new Date(now().getTime() + retryDelay(attemptCount)),
      lastError: message,
      lockedUntil: null,
    });
    logger.warn?.(`Civic classification for CivicSocial post ${delivery.originalPostId} will retry: ${message}`);
  }

  async function findOfficialAccount() {
    const official = await User.findOne({ where: { handle: "mybmc" } });
    return official?.isOfficial === true ? official : null;
  }

  function trackingUrl(citizenUrl, reference) {
    return `${citizenUrl}/track?reference=${encodeURIComponent(reference)}`;
  }

  async function acknowledge(delivery, originalPostId, reference, citizenUrl) {
    const official = await findOfficialAccount();
    if (!official) {
      await delivery.update({ status: "registered", lastError: "BMC complaint is registered; provision the @mybmc account with isOfficial=true to publish its acknowledgement.", lockedUntil: null, nextAttemptAt: new Date(now().getTime() + 5 * 60_000) });
      const log = logger.warn || logger.error;
      log?.call(logger, `BMC complaint ${originalPostId} is registered but the official @mybmc account is missing or not official`);
      return;
    }

    const marker = `BMC complaint ${reference} registered.`;
    const existingReplies = await Post.findAll({ where: { parentPostId: Number(originalPostId), authorId: official.id } });
    const existingAck = existingReplies.find(reply => String(reply.text || "").includes(marker));
    if (existingAck) {
      await delivery.update({ status: "acknowledged", acknowledgementPostId: existingAck.id, lastError: null, lockedUntil: null, nextAttemptAt: null });
      return;
    }

    const link = trackingUrl(citizenUrl, reference);
    const reply = await socialPlatform.createPost({
      authorId: official.id,
      text: `${marker} Track it here: ${link}`,
      parentPostId: Number(originalPostId),
    });
    if (!reply) throw new Error("BMC complaint is registered but its CivicSocial parent post could not be found for acknowledgement");
    await delivery.update({ status: "acknowledged", acknowledgementPostId: reply.id, lastError: null, lockedUntil: null, nextAttemptAt: null });
  }

  async function sendComplaint(delivery, post) {
    let endpoint;
    let citizenUrl;
    try {
      ({ endpoint, citizenUrl } = getConfiguration());
      const sourcePostUrl = complaintUrl(config.frontendUrl, post.id);
      const image = buildMediaPayload(post.image_data, "image");
      const video = buildMediaPayload(post.video_data, "video");
      const warnings = [image.warning, video.warning].filter(Boolean);
      if (warnings.length) logger.warn?.(`BMC media for CivicSocial post ${delivery.originalPostId}: ${warnings.join("; ")}`);
      const payload = {
        original_post_id: delivery.originalPostId,
        text: String(post.text),
        author: String(post.author.handle),
        source_post_url: sourcePostUrl,
      };
      if (image.value) payload.image_base64 = image.value;
      if (video.value) payload.video_base64 = video.value;
      await delivery.update({ attemptCount: delivery.attemptCount + 1, mediaWarning: warnings.length ? warnings.join("; ") : null });

      let response;
      try {
        response = await fetchImpl(endpoint, {
          method: "POST",
          headers: {
            "content-type": "application/json",
            authorization: `Bearer ${config.bmcIntegrationSecret}`,
            "idempotency-key": delivery.originalPostId,
          },
          body: JSON.stringify(payload),
          // BMC performs CPU inference and multilingual geocoding before it
          // replies. Give its idempotent delivery request room to finish;
          // the social post endpoint itself returns as soon as the durable
          // outbox row has been created (see processPost below).
          signal: AbortSignal.timeout(45_000),
          redirect: "error",
        });
      } catch (error) {
        await markRetry(delivery, error, { incrementAttempt: false });
        return;
      }

      if (!response.ok) {
        const retryable = response.status === 408 || response.status === 429 || response.status >= 500;
        const category = response.status === 401 || response.status === 403 ? "authentication" : retryable ? "temporary delivery" : "BMC validation";
        await markRetry(delivery, new Error(`${category} error: BMC returned HTTP ${response.status}`), { retryable });
        return;
      }

      let body;
      try { body = await response.json(); }
      catch { body = null; }
      if (!body || typeof body.complaint_reference !== "string" || !body.complaint_reference.trim()) {
        await markRetry(delivery, new Error("BMC returned an invalid response without complaint_reference (protocol error)"));
        return;
      }
      const complaintReference = body.complaint_reference.trim();
      await delivery.update({
        status: "registered",
        complaintReference,
        lastError: null,
        nextAttemptAt: null,
        lockedUntil: null,
      });
      logger.info?.(`BMC delivery for CivicSocial post ${delivery.originalPostId} registered as ${complaintReference}`);
      try { await acknowledge(delivery, String(post.id), complaintReference, citizenUrl); }
      catch (error) {
        const message = errorMessage(error, config.bmcIntegrationSecret);
        await delivery.update({ status: "registered", lastError: message, lockedUntil: null, nextAttemptAt: new Date(now().getTime() + retryDelay(Math.max(delivery.attemptCount, 1))) });
        logger.error?.(`BMC acknowledgement retry for CivicSocial post ${delivery.originalPostId}: ${message}`);
      }
    } catch (error) {
      const configurationError = /BMC_API_BASE_URL|BMC_INTEGRATION_SECRET|BMC_CITIZEN_FRONTEND_URL|FRONTEND_URL/.test(error.message);
      await markRetry(delivery, error, { retryable: true, delayMs: configurationError ? 5 * 60_000 : undefined });
    }
  }

  async function processDelivery(delivery) {
    const phase = await claim(delivery);
    if (!phase) {
      logger.info?.(`BMC_DELIVERY_CLAIM_SKIPPED post=${delivery.originalPostId} status=${delivery.status} reference=${delivery.complaintReference || "none"}`);
      return outcome(delivery);
    }
    if (phase === "ack") {
      try {
        const { citizenUrl } = getConfiguration();
        await acknowledge(delivery, socialPostId(delivery.originalPostId), delivery.complaintReference, citizenUrl);
      } catch (error) {
        const message = errorMessage(error, config.bmcIntegrationSecret);
        await delivery.update({ status: "registered", lastError: message, lockedUntil: null, nextAttemptAt: new Date(now().getTime() + retryDelay(Math.max(delivery.attemptCount, 1))) });
        logger.error?.(`BMC acknowledgement retry for CivicSocial post ${delivery.originalPostId}: ${message}`);
      }
      return outcome(delivery);
    }

    const post = await Post.findByPk(Number(socialPostId(delivery.originalPostId)), { include: [{ model: User, as: "author" }] });
    if (!post) {
      await markRetry(delivery, new Error("Original CivicSocial post is missing"), { retryable: false });
      return outcome(delivery);
    }
    const serializedPost = {
      id: post.id,
      text: post.text,
      image_data: post.imageData,
      video_data: post.videoData,
      parent_post_id: post.parentPostId,
      author: { handle: post.author?.handle, is_official: post.author?.isOfficial },
    };
    if (!isIntegrationCandidate(serializedPost)) {
      await delivery.update({ status: "failed", lastError: "Original post no longer meets BMC forwarding eligibility", lockedUntil: null });
      return outcome(delivery);
    }

    let classification;
    try {
      classification = await classifyPost(String(serializedPost.text || ""));
      if (!classification || !["civic_complaint", "not_civic_complaint", "uncertain"].includes(classification.classification)
        || !BMC_CATEGORIES.includes(classification.category)
        || typeof classification.reason !== "string") {
        throw new Error("Groq returned an unsupported civic classification");
      }
    } catch (error) {
      await markClassificationRetry(delivery, error);
      return outcome(delivery, null, "unavailable", "Civic issue detection could not be completed; it will retry automatically.");
    }

    // Groq is the primary classifier. If it rejects or is unsure about a
    // clearly stated issue, use a narrow multilingual safety net for explicit
    // malfunctioning signals and accumulated garbage. Provider errors still
    // follow the retry path above; they are never treated as a positive result.
    const fallback = classification.classification === "civic_complaint"
      ? null
      : inferClearCivicComplaint(serializedPost.text);
    if (fallback) {
      classification = {
        classification: "civic_complaint",
        category: fallback.category,
        reason: fallback.reason,
      };
      logger.info?.(`BMC_CLASSIFICATION post=${delivery.originalPostId} result=civic_complaint source=explicit_issue_fallback category=${classification.category}`);
    } else {
      logger.info?.(`BMC_CLASSIFICATION post=${delivery.originalPostId} result=${classification.classification} source=groq category=${classification.category}`);
    }

    if (classification.classification !== "civic_complaint") {
      const status = classification.classification === "uncertain" ? "uncertain" : "not_civic";
      await delivery.update({
        status,
        lastError: classification.reason.slice(0, 240),
        nextAttemptAt: null,
        lockedUntil: null,
      });
      return outcome(delivery, classification);
    }

    await sendComplaint(delivery, serializedPost);
    return outcome(delivery, classification);
  }

  return {
    isEligible: isIntegrationCandidate,

    async processPost(post) {
      if (!isIntegrationCandidate(post)) {
        if (mentionsMyBmc(post?.text)) {
          logger.info?.(`BMC_DELIVERY_SKIPPED post=${String(post?.id ?? "unknown")} reason=ineligible_author`);
        }
        return null;
      }
      logger.info?.(`BMC_DELIVERY_START post=${String(post.id)} reason=mybmc_mention`);
      try {
        const delivery = await createOrFind(post);
        // Persist first, then process outside the HTTP request. A timeout at
        // the browser or proxy must not make an already-published post look
        // failed. The durable row is recovered after restart and all retries
        // use BMC's original_post_id idempotency key.
        setImmediate(() => {
          processDelivery(delivery).then(result => {
            logger.info?.(`BMC_DELIVERY_OUTCOME post=${String(post.id)} status=${result?.status || "unknown"} classification_status=${result?.classificationStatus || "completed"} reference=${result?.complaintReference || "none"}`);
          }).catch(error => {
            logger.error?.(`BMC background delivery failed for CivicSocial post ${post.id}: ${errorMessage(error, config.bmcIntegrationSecret)}`);
          });
        });
        return outcome(delivery, null, "queued", "BMC forwarding is continuing in the background.");
      } catch (error) {
        logger.error?.(`Could not persist BMC delivery state for CivicSocial post ${post.id}: ${errorMessage(error, config.bmcIntegrationSecret)}`);
        return {
          status: "delivery_state_unavailable",
          complaintReference: null,
          lastError: "The CivicSocial post was published, but its BMC delivery state could not be saved.",
          classification: null,
          category: "unknown",
          reason: "A safe BMC retry is not available until delivery state is saved.",
          classificationStatus: "unavailable",
        };
      }
    },

    async processPending(limit = 50) {
      const candidates = await Delivery.findAll({
        where: { status: { [operators.in]: ["pending", "retry", "classification_retry", "sending", "registered", "acknowledging"] } },
        order: [["id", "ASC"]],
      });
      const at = now();
      const due = candidates.filter(row => {
        if (["retry", "classification_retry", "registered"].includes(row.status) && row.nextAttemptAt && row.nextAttemptAt > at) return false;
        if (["sending", "acknowledging"].includes(row.status) && row.lockedUntil && row.lockedUntil > at) return false;
        return true;
      }).slice(0, limit);
      for (const row of due) {
        try { await processDelivery(row); }
        catch (error) { logger.error?.(`BMC delivery retry failed for CivicSocial post ${row.originalPostId}: ${errorMessage(error, config.bmcIntegrationSecret)}`); }
      }
    },
  };
}
