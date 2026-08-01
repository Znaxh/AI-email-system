"""TF-IDF retrieval over the past-ticket corpus: given a new incoming email,
find the most similar historical (email, actual_reply) pairs.

Generic: works on any list of Ticket objects regardless of company/domain.
"""

from __future__ import annotations

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from src.company_data.schema import MIN_USEFUL_TONE_CORPUS
from src.schema import Ticket


class TicketRetriever:
    def __init__(
        self,
        tickets: list[Ticket],
        *,
        disable_neighbors: bool | None = None,
    ):
        """Fit on the corpus split only — holdout tickets must never be
        retrievable, or the test set would leak into generation.

        When the corpus is empty or below MIN_USEFUL_TONE_CORPUS, neighbor
        retrieval is disabled (disable_neighbors=True or auto). Generation then
        uses policy + transaction context only — tone may be blander, never wrong.
        """
        # Normalize split defensively (ingestion should already have done this).
        normalized = []
        for t in tickets:
            split = (t.split or "corpus").strip().lower()
            if split != "holdout":
                split = "corpus"
            if split == "corpus":
                normalized.append(t.model_copy(update={"split": "corpus"}))
        self.tickets = normalized
        auto_weak = len(self.tickets) < MIN_USEFUL_TONE_CORPUS
        self.disable_neighbors = auto_weak if disable_neighbors is None else bool(disable_neighbors)
        self.vectorizer = None
        self.matrix = None
        if self.tickets and not self.disable_neighbors:
            self.vectorizer = TfidfVectorizer(stop_words="english", ngram_range=(1, 2))
            self.matrix = self.vectorizer.fit_transform(
                [t.incoming_email for t in self.tickets]
            )

    def top_k(self, text: str, k: int = 3) -> list[Ticket]:
        if self.disable_neighbors or not self.tickets or self.vectorizer is None or self.matrix is None:
            return []
        query_vec = self.vectorizer.transform([text])
        scores = cosine_similarity(query_vec, self.matrix)[0]
        top = scores.argsort()[::-1][:k]
        return [self.tickets[i] for i in top]
