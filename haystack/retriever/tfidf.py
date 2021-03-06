import logging
from collections import OrderedDict, namedtuple
from typing import List

import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer

from haystack.database.base import BaseDocumentStore, Document
from haystack.retriever.base import BaseRetriever

logger = logging.getLogger(__name__)

# TODO make Paragraph generic for configurable units of text eg, pages, paragraphs, or split by a char_limit
Paragraph = namedtuple("Paragraph", ["paragraph_id", "document_id", "text", "meta"])


class TfidfRetriever(BaseRetriever):
    """
    Read all documents from a SQL backend.

    Split documents into smaller units (eg, paragraphs or pages) to reduce the 
    computations when text is passed on to a Reader for QA.

    It uses sklearn's TfidfVectorizer to compute a tf-idf matrix.
    """

    def __init__(self, document_store: BaseDocumentStore):
        self.vectorizer = TfidfVectorizer(
            lowercase=True,
            stop_words=None,
            token_pattern=r"(?u)\b\w\w+\b",
            ngram_range=(1, 1),
        )

        self.document_store = document_store
        self.paragraphs = self._get_all_paragraphs()
        self.df = None
        self.fit()

    def _get_all_paragraphs(self) -> List[Paragraph]:
        """
        Split the list of documents in paragraphs
        """
        documents = self.document_store.get_all_documents()

        paragraphs = []
        p_id = 0
        for doc in documents:
            for p in doc.text.split("\n\n"):  # TODO: this assumes paragraphs are separated by "\n\n". Can be switched to paragraph tokenizer.
                if not p.strip():  # skip empty paragraphs
                    continue
                paragraphs.append(
                    Paragraph(document_id=doc.id, paragraph_id=p_id, text=(p,), meta=doc.meta)
                )
                p_id += 1
        logger.info(f"Found {len(paragraphs)} candidate paragraphs from {len(documents)} docs in DB")
        return paragraphs

    def _calc_scores(self, query: str) -> dict:
        question_vector = self.vectorizer.transform([query])

        scores = self.tfidf_matrix.dot(question_vector.T).toarray()
        idx_scores = [(idx, score) for idx, score in enumerate(scores)]
        indices_and_scores = OrderedDict(
            sorted(idx_scores, key=(lambda tup: tup[1]), reverse=True)
        )
        return indices_and_scores

    def retrieve(self, query: str, filters: dict = None, top_k: int = 10, index: str = None) -> List[Document]:
        if filters:
            raise NotImplementedError("Filters are not implemented in TfidfRetriever.")
        if index:
            raise NotImplementedError("Switching index is not supported in TfidfRetriever.")

        # get scores
        indices_and_scores = self._calc_scores(query)

        # rank paragraphs
        df_sliced = self.df.loc[indices_and_scores.keys()]  # type: ignore
        df_sliced = df_sliced[:top_k]

        logger.debug(
            f"Identified {df_sliced.shape[0]} candidates via retriever:\n {df_sliced.to_string(col_space=10, index=False)}"
        )

        # get actual content for the top candidates
        paragraphs = list(df_sliced.text.values)
        meta_data = [{"document_id": row["document_id"], "paragraph_id": row["paragraph_id"],  "meta": row.get("meta", {})}
                     for idx, row in df_sliced.iterrows()]

        documents = []
        for para, meta in zip(paragraphs, meta_data):
            documents.append(
                Document(
                    id=meta["paragraph_id"],
                    text=para,
                    meta=meta.get("meta", {})
                ))

        return documents

    def fit(self):
        self.df = pd.DataFrame.from_dict(self.paragraphs)
        self.df["text"] = self.df["text"].apply(lambda x: " ".join(x))
        self.tfidf_matrix = self.vectorizer.fit_transform(self.df["text"])
