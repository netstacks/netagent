"""Text chunking for RAG."""

import re
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class Chunk:
    """A chunk of text with metadata."""

    text: str
    index: int
    section: Optional[str] = None
    metadata: Optional[dict] = None


class TextChunker:
    """Split text into overlapping chunks for embedding.

    Uses sentence boundaries where possible to create more semantically
    meaningful chunks.

    Usage:
        chunker = TextChunker(chunk_size=1000, overlap=200)
        chunks = chunker.chunk_text(document_text)
    """

    def __init__(
        self,
        chunk_size: int = 1000,
        overlap: int = 200,
        min_chunk_size: int = 100,
    ):
        """Initialize chunker.

        Args:
            chunk_size: Target size for each chunk in characters
            overlap: Number of characters to overlap between chunks
            min_chunk_size: Minimum size for a chunk (smaller chunks are merged)
        """
        self.chunk_size = chunk_size
        self.overlap = overlap
        self.min_chunk_size = min_chunk_size

        # Sentence boundary pattern
        self.sentence_pattern = re.compile(
            r'(?<=[.!?])\s+(?=[A-Z])|'  # Period/exclamation/question followed by capital
            r'\n\n+'  # Double newlines
        )

    def chunk_text(
        self,
        text: str,
        title: Optional[str] = None,
    ) -> List[Chunk]:
        """Split text into overlapping chunks.

        Args:
            text: Text to chunk
            title: Optional document title for metadata

        Returns:
            List of Chunk objects
        """
        if not text or len(text.strip()) < self.min_chunk_size:
            if text.strip():
                return [Chunk(text=text.strip(), index=0, metadata={"title": title})]
            return []

        # Split into sentences/paragraphs
        segments = self._split_into_segments(text)

        # Combine segments into chunks
        chunks = []
        current_chunk = []
        current_length = 0
        chunk_index = 0

        for segment in segments:
            segment_length = len(segment)

            # If single segment is larger than chunk size, split it
            if segment_length > self.chunk_size:
                # Save current chunk first
                if current_chunk:
                    chunk_text = " ".join(current_chunk)
                    chunks.append(Chunk(
                        text=chunk_text,
                        index=chunk_index,
                        metadata={"title": title},
                    ))
                    chunk_index += 1
                    current_chunk = []
                    current_length = 0

                # Split large segment
                for sub_chunk in self._split_large_segment(segment):
                    chunks.append(Chunk(
                        text=sub_chunk,
                        index=chunk_index,
                        metadata={"title": title},
                    ))
                    chunk_index += 1
                continue

            # Check if adding segment exceeds chunk size
            if current_length + segment_length > self.chunk_size:
                # Save current chunk
                chunk_text = " ".join(current_chunk)
                chunks.append(Chunk(
                    text=chunk_text,
                    index=chunk_index,
                    metadata={"title": title},
                ))
                chunk_index += 1

                # Start new chunk with overlap
                overlap_segments = self._get_overlap_segments(
                    current_chunk,
                    self.overlap
                )
                current_chunk = overlap_segments + [segment]
                current_length = sum(len(s) for s in current_chunk) + len(current_chunk) - 1
            else:
                current_chunk.append(segment)
                current_length += segment_length + 1  # +1 for space

        # Save last chunk
        if current_chunk:
            chunk_text = " ".join(current_chunk)
            if len(chunk_text) >= self.min_chunk_size:
                chunks.append(Chunk(
                    text=chunk_text,
                    index=chunk_index,
                    metadata={"title": title},
                ))
            elif chunks:
                # Merge with previous chunk if too small
                prev_chunk = chunks[-1]
                chunks[-1] = Chunk(
                    text=prev_chunk.text + " " + chunk_text,
                    index=prev_chunk.index,
                    metadata=prev_chunk.metadata,
                )

        return chunks

    def _split_into_segments(self, text: str) -> List[str]:
        """Split text into sentences/paragraphs."""
        # First split by double newlines
        paragraphs = re.split(r'\n\n+', text)

        segments = []
        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            # Split paragraph into sentences
            sentences = self.sentence_pattern.split(para)
            for sentence in sentences:
                sentence = sentence.strip()
                if sentence:
                    segments.append(sentence)

        return segments

    def _split_large_segment(self, segment: str) -> List[str]:
        """Split a large segment into smaller chunks."""
        chunks = []
        words = segment.split()
        current = []
        current_length = 0

        for word in words:
            word_length = len(word) + 1  # +1 for space
            if current_length + word_length > self.chunk_size:
                if current:
                    chunks.append(" ".join(current))
                current = [word]
                current_length = len(word)
            else:
                current.append(word)
                current_length += word_length

        if current:
            chunks.append(" ".join(current))

        return chunks

    def _get_overlap_segments(
        self,
        segments: List[str],
        overlap_size: int,
    ) -> List[str]:
        """Get segments from end to create overlap."""
        if not segments:
            return []

        result = []
        current_size = 0

        for segment in reversed(segments):
            if current_size + len(segment) > overlap_size:
                break
            result.insert(0, segment)
            current_size += len(segment) + 1

        return result

    def chunk_with_sections(
        self,
        text: str,
        title: Optional[str] = None,
    ) -> List[Chunk]:
        """Chunk text while preserving section information.

        Detects headers (lines starting with # or all caps) and includes
        section context in chunk metadata.

        Args:
            text: Text to chunk
            title: Optional document title

        Returns:
            List of Chunk objects with section metadata
        """
        # Detect sections
        sections = self._extract_sections(text)

        chunks = []
        chunk_index = 0

        for section_name, section_text in sections:
            section_chunks = self.chunk_text(section_text, title)

            for chunk in section_chunks:
                chunk.index = chunk_index
                chunk.section = section_name
                if chunk.metadata:
                    chunk.metadata["section"] = section_name
                else:
                    chunk.metadata = {"title": title, "section": section_name}
                chunks.append(chunk)
                chunk_index += 1

        return chunks

    def _extract_sections(self, text: str) -> List[tuple]:
        """Extract sections from text based on headers."""
        # Match markdown-style headers or all-caps lines
        header_pattern = re.compile(
            r'^(#{1,6}\s+.+?)$|^([A-Z][A-Z\s]{5,})$',
            re.MULTILINE
        )

        sections = []
        last_end = 0
        current_section = "Introduction"

        for match in header_pattern.finditer(text):
            # Save previous section
            section_text = text[last_end:match.start()].strip()
            if section_text:
                sections.append((current_section, section_text))

            # Get new section name
            header = match.group(1) or match.group(2)
            current_section = header.strip("#").strip()
            last_end = match.end()

        # Add final section
        section_text = text[last_end:].strip()
        if section_text:
            sections.append((current_section, section_text))

        # If no sections found, return entire text
        if not sections:
            sections = [("Content", text)]

        return sections
