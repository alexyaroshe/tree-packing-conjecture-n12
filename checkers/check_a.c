// checker

#include <errno.h>
#include <inttypes.h>
#include <limits.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#define MAXN 12
#define MAXTREE 551
#define MAXEDGE 66
#ifndef CACHE_BITS
#define CACHE_BITS 16
#endif

static const unsigned audit_expected_counts[MAXN + 1] = {0,  0,  1,  1,   2,   3,  6,
                                                         11, 23, 47, 106, 235, 551};
static uint32_t audit_reference_codes[MAXN + 1][MAXTREE];
static unsigned char audit_pair_left[MAXEDGE], audit_pair_right[MAXEDGE];
static uint64_t audit_current_selection;
typedef struct
{
  uint64_t audit_lower_word;
  uint32_t audit_shape_code;
  uint8_t audit_upper_word;
} audit_SavedRecord;
static audit_SavedRecord audit_saved_entries[1u << CACHE_BITS];
static uint64_t audit_saved_hits, audit_saved_misses;

static void audit_report_failure(const char *audit_message_format, ...)
{
  va_list audit_format_arguments;
  va_start(audit_format_arguments, audit_message_format);
  fputs("FAIL: ", stderr);
  vfprintf(stderr, audit_message_format, audit_format_arguments);
  fputc('\n', stderr);
  va_end(audit_format_arguments);
  exit(1);
}

static unsigned audit_bit_count(unsigned audit_value)
{
  return (unsigned)__builtin_popcount(audit_value);
}
static unsigned audit_lower_word(unsigned audit_value)
{
  return (unsigned)__builtin_ctz(audit_value);
}

static int audit_check_connected(const uint16_t *audit_links, unsigned audit_node_mask)
{
  unsigned audit_reached_mask = audit_node_mask & (0u - audit_node_mask);
  unsigned audit_pending_links = audit_reached_mask;
  while (audit_pending_links)
  {
    unsigned audit_node_index = audit_lower_word(audit_pending_links);
    audit_pending_links &= audit_pending_links - 1;
    unsigned audit_new_links = audit_links[audit_node_index] & ~audit_reached_mask;
    audit_reached_mask |= audit_new_links;
    audit_pending_links |= audit_new_links;
  }
  return audit_reached_mask == audit_node_mask;
}

static uint32_t audit_encode_branch(const uint16_t *audit_links,
                                    unsigned audit_node_index,
                                    unsigned audit_previous_node)
{
  uint32_t audit_child_codes[MAXN];
  unsigned audit_match_count = 0;
  unsigned audit_neighbor_mask =
      audit_links[audit_node_index] & ~(1u << audit_previous_node);
  while (audit_neighbor_mask)
  {
    unsigned audit_adjacent_index = audit_lower_word(audit_neighbor_mask);
    audit_neighbor_mask &= audit_neighbor_mask - 1;
    uint32_t audit_shape_code =
        audit_encode_branch(audit_links, audit_adjacent_index, audit_node_index);
    unsigned audit_cursor_position = audit_match_count++;
    while (audit_cursor_position &&
           audit_child_codes[audit_cursor_position - 1] > audit_shape_code)
    {
      audit_child_codes[audit_cursor_position] =
          audit_child_codes[audit_cursor_position - 1];
      --audit_cursor_position;
    }
    audit_child_codes[audit_cursor_position] = audit_shape_code;
  }
  uint32_t audit_shape_code = 1;
  for (unsigned audit_position = 0; audit_position < audit_match_count;
       ++audit_position)
  {
    unsigned audit_record_length =
        32u - (unsigned)__builtin_clz(audit_child_codes[audit_position]);
    audit_shape_code =
        (audit_shape_code << audit_record_length) | audit_child_codes[audit_position];
  }
  return audit_shape_code << 1;
}

static uint32_t audit_normalize_shape(const uint16_t *audit_links,
                                      unsigned audit_node_mask)
{
  unsigned audit_remaining_mask = audit_node_mask;
  while (audit_bit_count(audit_remaining_mask) > 2)
  {
    unsigned audit_tip_nodes = 0;
    unsigned audit_parse_cursor = audit_remaining_mask;
    while (audit_parse_cursor)
    {
      unsigned audit_node_index = audit_lower_word(audit_parse_cursor);
      audit_parse_cursor &= audit_parse_cursor - 1;
      if (audit_bit_count(audit_links[audit_node_index] & audit_remaining_mask) <= 1)
        audit_tip_nodes |= 1u << audit_node_index;
    }
    if (!audit_tip_nodes)
      audit_report_failure("canonicalization requires a tree");
    audit_remaining_mask &= ~audit_tip_nodes;
  }
  unsigned audit_first_value = audit_lower_word(audit_remaining_mask);
  audit_remaining_mask &= audit_remaining_mask - 1;
  if (!audit_remaining_mask)
    return audit_encode_branch(audit_links, audit_first_value, MAXN);
  unsigned audit_second_value = audit_lower_word(audit_remaining_mask);
  uint32_t audit_left_code =
      audit_encode_branch(audit_links, audit_first_value, audit_second_value);
  uint32_t audit_right_code =
      audit_encode_branch(audit_links, audit_second_value, audit_first_value);
  unsigned audit_left_size = 32u - (unsigned)__builtin_clz(audit_left_code);
  unsigned audit_right_size = 32u - (unsigned)__builtin_clz(audit_right_code);
  uint32_t audit_forward_code =
      (audit_left_code << audit_right_size) | audit_right_code;
  uint32_t audit_reverse_code = (audit_right_code << audit_left_size) | audit_left_code;
  return audit_forward_code < audit_reverse_code ? audit_forward_code
                                                 : audit_reverse_code;
}

static unsigned audit_locate_saved(uint64_t audit_lower_key, unsigned audit_upper_key)
{
  uint64_t audit_fingerprint =
      audit_lower_key ^ ((uint64_t)audit_upper_key * UINT64_C(0x9e3779b97f4a7c15));
  audit_fingerprint ^= audit_fingerprint >> 30;
  audit_fingerprint *= UINT64_C(0xbf58476d1ce4e5b9);
  audit_fingerprint ^= audit_fingerprint >> 27;
  return (unsigned)audit_fingerprint & ((1u << CACHE_BITS) - 1);
}

static unsigned audit_read_row(char *audit_input_line, long *audit_row_values,
                               unsigned audit_storage_capacity)
{
  unsigned audit_occupied_mask = 0;
  char *audit_parent_index = audit_input_line;
  while (*audit_parent_index)
  {
    while (*audit_parent_index == ' ' || *audit_parent_index == '\t' ||
           *audit_parent_index == '\r' || *audit_parent_index == '\n')
      ++audit_parent_index;
    if (!*audit_parent_index)
      break;
    if (audit_occupied_mask == audit_storage_capacity)
      audit_report_failure("too many integers in tree row");
    char *audit_end_pointer;
    errno = 0;
    audit_row_values[audit_occupied_mask++] =
        strtol(audit_parent_index, &audit_end_pointer, 10);
    if (errno || audit_end_pointer == audit_parent_index)
      audit_report_failure("invalid integer in tree file");
    if (*audit_end_pointer && *audit_end_pointer != ' ' && *audit_end_pointer != '\t' &&
        *audit_end_pointer != '\r' && *audit_end_pointer != '\n')
      audit_report_failure("invalid separator in tree file");
    audit_parent_index = audit_end_pointer;
  }
  return audit_occupied_mask;
}

static void audit_read_catalogue(const char *audit_file_path, unsigned audit_host_size)
{
  FILE *audit_record_file = fopen(audit_file_path, "r");
  if (!audit_record_file)
    audit_report_failure("cannot open trees: %s", strerror(errno));
  char *audit_input_line = NULL;
  size_t audit_storage_capacity = 0;
  unsigned audit_current_section = 0, audit_visited_mask[MAXN + 1] = {0},
           audit_header_count[MAXN + 1] = {0};
  ssize_t audit_record_length;
  while ((audit_record_length = getline(&audit_input_line, &audit_storage_capacity,
                                        audit_record_file)) >= 0)
  {
    if (memchr(audit_input_line, '\0', (size_t)audit_record_length))
      audit_report_failure("NUL in tree file");
    char *audit_parent_index = audit_input_line;
    while (*audit_parent_index == ' ' || *audit_parent_index == '\t' ||
           *audit_parent_index == '\r' || *audit_parent_index == '\n')
      ++audit_parent_index;
    if (!*audit_parent_index)
      continue;
    long audit_row_values[MAXN + 1];
    if (*audit_parent_index == '#')
    {
      if (audit_read_row(audit_parent_index + 1, audit_row_values, 1) != 1 ||
          audit_row_values[0] < 2 || audit_row_values[0] > MAXN)
        audit_report_failure("invalid tree section");
      audit_current_section = (unsigned)audit_row_values[0];
      if (audit_header_count[audit_current_section]++)
        audit_report_failure("duplicate tree section %u", audit_current_section);
      continue;
    }
    if (!audit_current_section ||
        audit_read_row(audit_parent_index, audit_row_values, MAXN + 1) !=
            audit_current_section ||
        audit_row_values[0] != 0)
      audit_report_failure("invalid parent row at k=%u", audit_current_section);
    if (audit_visited_mask[audit_current_section] >=
        audit_expected_counts[audit_current_section])
      audit_report_failure("too many reference trees at k=%u", audit_current_section);
    uint16_t audit_links[MAXN] = {0};
    for (unsigned audit_node_index = 1; audit_node_index < audit_current_section;
         ++audit_node_index)
    {
      if (audit_row_values[audit_node_index] < 1 ||
          audit_row_values[audit_node_index] > (long)audit_current_section)
        audit_report_failure("invalid parent at k=%u", audit_current_section);
      unsigned audit_parent_node = (unsigned)audit_row_values[audit_node_index] - 1;
      if (audit_parent_node == audit_node_index ||
          (audit_links[audit_node_index] & (1u << audit_parent_node)))
        audit_report_failure("invalid reference edge");
      audit_links[audit_node_index] |= (uint16_t)(1u << audit_parent_node);
      audit_links[audit_parent_node] |= (uint16_t)(1u << audit_node_index);
    }
    unsigned audit_node_mask = (1u << audit_current_section) - 1;
    if (!audit_check_connected(audit_links, audit_node_mask))
      audit_report_failure("disconnected reference tree");
    uint32_t audit_shape_code = audit_normalize_shape(audit_links, audit_node_mask);
    for (unsigned j = 0; j < audit_visited_mask[audit_current_section]; ++j)
      if (audit_reference_codes[audit_current_section][j] == audit_shape_code)
        audit_report_failure("duplicate reference shape at k=%u",
                             audit_current_section);
    audit_reference_codes[audit_current_section]
                         [audit_visited_mask[audit_current_section]++] =
                             audit_shape_code;
  }
  if (ferror(audit_record_file))
    audit_report_failure("tree read error");
  free(audit_input_line);
  if (fclose(audit_record_file))
    audit_report_failure("tree close error");
  for (unsigned audit_record_size = 2; audit_record_size <= MAXN; ++audit_record_size)
    if ((audit_record_size <= audit_host_size ||
         audit_header_count[audit_record_size]) &&
        audit_visited_mask[audit_record_size] !=
            audit_expected_counts[audit_record_size])
      audit_report_failure("reference count k=%u: %u != %u", audit_record_size,
                           audit_visited_mask[audit_record_size],
                           audit_expected_counts[audit_record_size]);
}

static void audit_check_selection(const unsigned char *audit_check_record,
                                  unsigned audit_host_size, unsigned audit_pair_total)
{
  unsigned audit_selection[MAXN + 1];
  uint64_t audit_value = audit_current_selection;
  for (unsigned audit_record_size = 2; audit_record_size <= audit_host_size;
       ++audit_record_size)
  {
    audit_selection[audit_record_size] =
        (unsigned)(audit_value % audit_expected_counts[audit_record_size]);
    audit_value /= audit_expected_counts[audit_record_size];
  }
  uint64_t audit_pair_values[2] = {0, 0};
  unsigned audit_read_offset = 0;
  for (unsigned audit_record_size = audit_host_size; audit_record_size >= 2;
       --audit_record_size)
  {
    unsigned audit_first_index = audit_read_offset;
    uint64_t audit_entry_key[2] = {0, 0};
    for (unsigned j = 0; j < audit_record_size - 1; ++j)
    {
      unsigned audit_pair_index = audit_check_record[audit_read_offset++];
      if (audit_pair_index >= audit_pair_total)
        audit_report_failure("E2 index=%" PRIu64 " edge=%u", audit_current_selection,
                             audit_pair_index);
      uint64_t audit_first_bit = UINT64_C(1) << (audit_pair_index & 63);
      if (audit_pair_values[audit_pair_index >> 6] & audit_first_bit)
        audit_report_failure("E1 index=%" PRIu64 " duplicate edge=%u",
                             audit_current_selection, audit_pair_index);
      audit_pair_values[audit_pair_index >> 6] |= audit_first_bit;
      audit_entry_key[audit_pair_index >> 6] |= audit_first_bit;
    }
    audit_SavedRecord *audit_saved_entry = &audit_saved_entries[audit_locate_saved(
        audit_entry_key[0], (unsigned)audit_entry_key[1])];
    uint32_t audit_shape_code;
    if (audit_saved_entry->audit_shape_code &&
        audit_saved_entry->audit_lower_word == audit_entry_key[0] &&
        audit_saved_entry->audit_upper_word == audit_entry_key[1])
    {
      audit_shape_code = audit_saved_entry->audit_shape_code;
      audit_saved_hits++;
    }
    else
    {
      uint16_t audit_links[MAXN] = {0};
      unsigned audit_node_mask = 0;
      for (unsigned audit_cursor_position = audit_first_index;
           audit_cursor_position < audit_read_offset; ++audit_cursor_position)
      {
        unsigned audit_pair_index = audit_check_record[audit_cursor_position];
        unsigned u = audit_pair_left[audit_pair_index],
                 audit_node_index = audit_pair_right[audit_pair_index];
        audit_links[u] |= (uint16_t)(1u << audit_node_index);
        audit_links[audit_node_index] |= (uint16_t)(1u << u);
        audit_node_mask |= (1u << u) | (1u << audit_node_index);
      }
      if (audit_bit_count(audit_node_mask) != audit_record_size)
        audit_report_failure("E3 index=%" PRIu64 " k=%u", audit_current_selection,
                             audit_record_size);
      audit_shape_code = audit_normalize_shape(audit_links, audit_node_mask);
      audit_saved_entry->audit_lower_word = audit_entry_key[0];
      audit_saved_entry->audit_upper_word = (uint8_t)audit_entry_key[1];
      audit_saved_entry->audit_shape_code = audit_shape_code;
      audit_saved_misses++;
    }
    if (audit_shape_code !=
        audit_reference_codes[audit_record_size][audit_selection[audit_record_size]])
      audit_report_failure("E5 index=%" PRIu64 " k=%u tree=%u", audit_current_selection,
                           audit_record_size, audit_selection[audit_record_size]);
  }
  if (audit_read_offset != audit_pair_total)
    audit_report_failure("internal block accounting error");
}

static uint64_t audit_parsed_number(const char *audit_text_value)
{
  if (!*audit_text_value || *audit_text_value == '-' || *audit_text_value == '+' ||
      *audit_text_value == ' ')
    audit_report_failure("invalid nonnegative integer");
  char *audit_end_pointer;
  errno = 0;
  unsigned long long audit_parsed_value =
      strtoull(audit_text_value, &audit_end_pointer, 10);
  if (errno || *audit_end_pointer)
    audit_report_failure("invalid nonnegative integer: %s", audit_text_value);
  return (uint64_t)audit_parsed_value;
}

int main(int audit_argument_count, char **audit_argument_values)
{
  uint64_t audit_order_argument = 0, audit_slice_index = 0, audit_stride_size = 1,
           audit_read_limit = 0;
  const char *audit_catalogue = NULL, *audit_output_record = NULL;
  unsigned audit_read_options = 0;
  for (int audit_position = 1; audit_position < audit_argument_count;
       audit_position += 2)
  {
    if (audit_position + 1 >= audit_argument_count)
      audit_report_failure("missing option value");
    unsigned audit_first_bit = 0;
    if (!strcmp(audit_argument_values[audit_position], "--n"))
    {
      audit_first_bit = 1;
      audit_order_argument =
          audit_parsed_number(audit_argument_values[audit_position + 1]);
    }
    else if (!strcmp(audit_argument_values[audit_position], "--trees"))
    {
      audit_first_bit = 2;
      audit_catalogue = audit_argument_values[audit_position + 1];
    }
    else if (!strcmp(audit_argument_values[audit_position], "--cert"))
    {
      audit_first_bit = 4;
      audit_output_record = audit_argument_values[audit_position + 1];
    }
    else if (!strcmp(audit_argument_values[audit_position], "--res"))
    {
      audit_first_bit = 8;
      audit_slice_index =
          audit_parsed_number(audit_argument_values[audit_position + 1]);
    }
    else if (!strcmp(audit_argument_values[audit_position], "--mod"))
    {
      audit_first_bit = 16;
      audit_stride_size =
          audit_parsed_number(audit_argument_values[audit_position + 1]);
    }
    else if (!strcmp(audit_argument_values[audit_position], "--limit"))
    {
      audit_first_bit = 32;
      audit_read_limit = audit_parsed_number(audit_argument_values[audit_position + 1]);
    }
    else
      audit_report_failure("unknown option: %s", audit_argument_values[audit_position]);
    if (audit_read_options & audit_first_bit)
      audit_report_failure("duplicate option: %s",
                           audit_argument_values[audit_position]);
    audit_read_options |= audit_first_bit;
  }
  if ((audit_read_options & 7) != 7 || audit_order_argument < 2 ||
      audit_order_argument > MAXN || !audit_stride_size ||
      (audit_slice_index >= audit_stride_size && !audit_read_limit))
    audit_report_failure(
        "usage: --n 2..12 --trees FILE --cert FILE [--res R --mod M] [--limit K]");
  unsigned audit_host_size = (unsigned)audit_order_argument,
           audit_pair_total = audit_host_size * (audit_host_size - 1) / 2;
  audit_read_catalogue(audit_catalogue, audit_host_size);
  unsigned audit_pair_index = 0;
  for (unsigned u = 0; u < audit_host_size; ++u)
    for (unsigned audit_node_index = u + 1; audit_node_index < audit_host_size;
         ++audit_node_index)
    {
      audit_pair_left[audit_pair_index] = u;
      audit_pair_right[audit_pair_index++] = audit_node_index;
    }
  uint64_t audit_sequence_count = 1;
  for (unsigned audit_record_size = 2; audit_record_size <= audit_host_size;
       ++audit_record_size)
    audit_sequence_count *= audit_expected_counts[audit_record_size];
  uint64_t audit_selection_slice =
      audit_slice_index < audit_sequence_count
          ? 1 + (audit_sequence_count - 1 - audit_slice_index) / audit_stride_size
          : 0;
  if (audit_read_limit > audit_selection_slice)
    audit_report_failure("limit exceeds slice cardinality");
  uint64_t audit_expected_total =
      audit_read_limit ? audit_read_limit : audit_selection_slice;
  FILE *audit_record_file = fopen(audit_output_record, "rb");
  if (!audit_record_file)
    audit_report_failure("cannot open certificate: %s", strerror(errno));
  struct stat audit_file_info;
  if (fstat(fileno(audit_record_file), &audit_file_info) ||
      !S_ISREG(audit_file_info.st_mode) || audit_file_info.st_size < 0)
    audit_report_failure("certificate must be a readable regular file");
  if ((uint64_t)audit_file_info.st_size != audit_expected_total * audit_pair_total)
    audit_report_failure("E6 bytes=%" PRIu64 " expected=%" PRIu64,
                         (uint64_t)audit_file_info.st_size,
                         audit_expected_total * audit_pair_total);
  static char audit_read_buffer[1 << 20];
  if (setvbuf(audit_record_file, audit_read_buffer, _IOFBF, sizeof audit_read_buffer))
    audit_report_failure("certificate buffer setup failed");
  unsigned char audit_check_record[MAXEDGE];
  audit_current_selection = audit_slice_index;
  for (uint64_t audit_position = 0; audit_position < audit_expected_total;
       ++audit_position)
  {
    if (fread(audit_check_record, 1, audit_pair_total, audit_record_file) !=
        audit_pair_total)
      audit_report_failure("E6 missing record=%" PRIu64, audit_position);
    audit_check_selection(audit_check_record, audit_host_size, audit_pair_total);
    if (audit_position + 1 < audit_expected_total)
      audit_current_selection += audit_stride_size;
  }
  if (fgetc(audit_record_file) != EOF || ferror(audit_record_file))
    audit_report_failure("E6 trailing data or read error");
  if (fclose(audit_record_file))
    audit_report_failure("certificate close error");
  fprintf(stderr, "CACHE hits=%" PRIu64 " misses=%" PRIu64 " bytes=%zu\n",
          audit_saved_hits, audit_saved_misses, sizeof audit_saved_entries);
  printf("VERIFIED%s n=%u slice=%" PRIu64 "/%" PRIu64 ": %" PRIu64
         " certificates, E1-E6 hold%s\n",
         audit_read_limit ? "-PREFIX" : "", audit_host_size, audit_slice_index,
         audit_stride_size, audit_expected_total,
         audit_read_limit ? " for the requested prefix" : "");
  return 0;
}
