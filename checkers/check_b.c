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

static const unsigned review_expected_counts[MAXN + 1] = {0,  0,  1,  1,   2,   3,  6,
                                                          11, 23, 47, 106, 235, 551};
static uint32_t review_reference_codes[MAXN + 1][MAXTREE];
static unsigned char review_pair_left[MAXEDGE], review_pair_right[MAXEDGE];
static uint64_t review_current_selection;

static void review_report_failure(const char *review_message_format, ...)
{
  va_list review_format_arguments;
  va_start(review_format_arguments, review_message_format);
  fputs("FAIL: ", stderr);
  vfprintf(stderr, review_message_format, review_format_arguments);
  fputc('\n', stderr);
  va_end(review_format_arguments);
  exit(1);
}

static unsigned review_bit_count(unsigned review_value)
{
  return (unsigned)__builtin_popcount(review_value);
}
static unsigned review_lower_word(unsigned review_value)
{
  return (unsigned)__builtin_ctz(review_value);
}

static int review_check_connected(const uint16_t *review_links,
                                  unsigned review_node_mask)
{
  unsigned review_reached_mask = review_node_mask & (0u - review_node_mask);
  unsigned review_pending_links = review_reached_mask;
  while (review_pending_links)
  {
    unsigned review_node_index = review_lower_word(review_pending_links);
    review_pending_links &= review_pending_links - 1;
    unsigned review_new_links = review_links[review_node_index] & ~review_reached_mask;
    review_reached_mask |= review_new_links;
    review_pending_links |= review_new_links;
  }
  return review_reached_mask == review_node_mask;
}

static uint32_t review_encode_branch(const uint16_t *review_links,
                                     unsigned review_node_index,
                                     unsigned review_previous_node)
{
  uint32_t review_child_codes[MAXN];
  unsigned review_match_count = 0;
  unsigned review_neighbor_mask =
      review_links[review_node_index] & ~(1u << review_previous_node);
  while (review_neighbor_mask)
  {
    unsigned review_adjacent_index = review_lower_word(review_neighbor_mask);
    review_neighbor_mask &= review_neighbor_mask - 1;
    uint32_t review_shape_code =
        review_encode_branch(review_links, review_adjacent_index, review_node_index);
    unsigned review_cursor_position = review_match_count++;
    while (review_cursor_position &&
           review_child_codes[review_cursor_position - 1] > review_shape_code)
    {
      review_child_codes[review_cursor_position] =
          review_child_codes[review_cursor_position - 1];
      --review_cursor_position;
    }
    review_child_codes[review_cursor_position] = review_shape_code;
  }
  uint32_t review_shape_code = 1;
  for (unsigned review_position = 0; review_position < review_match_count;
       ++review_position)
  {
    unsigned review_record_length =
        32u - (unsigned)__builtin_clz(review_child_codes[review_position]);
    review_shape_code = (review_shape_code << review_record_length) |
                        review_child_codes[review_position];
  }
  return review_shape_code << 1;
}

static uint32_t review_normalize_shape(const uint16_t *review_links,
                                       unsigned review_node_mask)
{
  unsigned review_remaining_mask = review_node_mask;
  while (review_bit_count(review_remaining_mask) > 2)
  {
    unsigned review_tip_nodes = 0;
    unsigned review_parse_cursor = review_remaining_mask;
    while (review_parse_cursor)
    {
      unsigned review_node_index = review_lower_word(review_parse_cursor);
      review_parse_cursor &= review_parse_cursor - 1;
      if (review_bit_count(review_links[review_node_index] & review_remaining_mask) <=
          1)
        review_tip_nodes |= 1u << review_node_index;
    }
    if (!review_tip_nodes)
      review_report_failure("canonicalization requires a tree");
    review_remaining_mask &= ~review_tip_nodes;
  }
  unsigned review_first_value = review_lower_word(review_remaining_mask);
  uint32_t review_shape_code =
      review_encode_branch(review_links, review_first_value, MAXN);
  review_remaining_mask &= review_remaining_mask - 1;
  if (review_remaining_mask)
  {
    uint32_t review_alternate_node = review_encode_branch(
        review_links, review_lower_word(review_remaining_mask), MAXN);
    if (review_alternate_node < review_shape_code)
      review_shape_code = review_alternate_node;
  }
  return review_shape_code;
}

static unsigned review_read_row(char *review_input_line, long *review_row_values,
                                unsigned review_storage_capacity)
{
  unsigned review_occupied_mask = 0;
  char *review_parent_index = review_input_line;
  while (*review_parent_index)
  {
    while (*review_parent_index == ' ' || *review_parent_index == '\t' ||
           *review_parent_index == '\r' || *review_parent_index == '\n')
      ++review_parent_index;
    if (!*review_parent_index)
      break;
    if (review_occupied_mask == review_storage_capacity)
      review_report_failure("too many integers in tree row");
    char *review_end_pointer;
    errno = 0;
    review_row_values[review_occupied_mask++] =
        strtol(review_parent_index, &review_end_pointer, 10);
    if (errno || review_end_pointer == review_parent_index)
      review_report_failure("invalid integer in tree file");
    if (*review_end_pointer && *review_end_pointer != ' ' &&
        *review_end_pointer != '\t' && *review_end_pointer != '\r' &&
        *review_end_pointer != '\n')
      review_report_failure("invalid separator in tree file");
    review_parent_index = review_end_pointer;
  }
  return review_occupied_mask;
}

static void review_read_catalogue(const char *review_file_path,
                                  unsigned review_host_size)
{
  FILE *review_record_file = fopen(review_file_path, "r");
  if (!review_record_file)
    review_report_failure("cannot open trees: %s", strerror(errno));
  char *review_input_line = NULL;
  size_t review_storage_capacity = 0;
  unsigned review_current_section = 0, review_visited_mask[MAXN + 1] = {0},
           review_header_count[MAXN + 1] = {0};
  ssize_t review_record_length;
  while ((review_record_length = getline(&review_input_line, &review_storage_capacity,
                                         review_record_file)) >= 0)
  {
    if (memchr(review_input_line, '\0', (size_t)review_record_length))
      review_report_failure("NUL in tree file");
    char *review_parent_index = review_input_line;
    while (*review_parent_index == ' ' || *review_parent_index == '\t' ||
           *review_parent_index == '\r' || *review_parent_index == '\n')
      ++review_parent_index;
    if (!*review_parent_index)
      continue;
    long review_row_values[MAXN + 1];
    if (*review_parent_index == '#')
    {
      if (review_read_row(review_parent_index + 1, review_row_values, 1) != 1 ||
          review_row_values[0] < 2 || review_row_values[0] > MAXN)
        review_report_failure("invalid tree section");
      review_current_section = (unsigned)review_row_values[0];
      if (review_header_count[review_current_section]++)
        review_report_failure("duplicate tree section %u", review_current_section);
      continue;
    }
    if (!review_current_section ||
        review_read_row(review_parent_index, review_row_values, MAXN + 1) !=
            review_current_section ||
        review_row_values[0] != 0)
      review_report_failure("invalid parent row at k=%u", review_current_section);
    if (review_visited_mask[review_current_section] >=
        review_expected_counts[review_current_section])
      review_report_failure("too many reference trees at k=%u", review_current_section);
    uint16_t review_links[MAXN] = {0};
    for (unsigned review_node_index = 1; review_node_index < review_current_section;
         ++review_node_index)
    {
      if (review_row_values[review_node_index] < 1 ||
          review_row_values[review_node_index] > (long)review_current_section)
        review_report_failure("invalid parent at k=%u", review_current_section);
      unsigned review_parent_node = (unsigned)review_row_values[review_node_index] - 1;
      if (review_parent_node == review_node_index ||
          (review_links[review_node_index] & (1u << review_parent_node)))
        review_report_failure("invalid reference edge");
      review_links[review_node_index] |= (uint16_t)(1u << review_parent_node);
      review_links[review_parent_node] |= (uint16_t)(1u << review_node_index);
    }
    unsigned review_node_mask = (1u << review_current_section) - 1;
    if (!review_check_connected(review_links, review_node_mask))
      review_report_failure("disconnected reference tree");
    uint32_t review_shape_code = review_normalize_shape(review_links, review_node_mask);
    for (unsigned j = 0; j < review_visited_mask[review_current_section]; ++j)
      if (review_reference_codes[review_current_section][j] == review_shape_code)
        review_report_failure("duplicate reference shape at k=%u",
                              review_current_section);
    review_reference_codes[review_current_section]
                          [review_visited_mask[review_current_section]++] =
                              review_shape_code;
  }
  if (ferror(review_record_file))
    review_report_failure("tree read error");
  free(review_input_line);
  if (fclose(review_record_file))
    review_report_failure("tree close error");
  for (unsigned review_record_size = 2; review_record_size <= MAXN;
       ++review_record_size)
    if ((review_record_size <= review_host_size ||
         review_header_count[review_record_size]) &&
        review_visited_mask[review_record_size] !=
            review_expected_counts[review_record_size])
      review_report_failure("reference count k=%u: %u != %u", review_record_size,
                            review_visited_mask[review_record_size],
                            review_expected_counts[review_record_size]);
}

static void review_check_selection(const unsigned char *review_check_record,
                                   unsigned review_host_size,
                                   unsigned review_pair_total)
{
  unsigned review_selection[MAXN + 1];
  uint64_t review_value = review_current_selection;
  for (unsigned review_record_size = 2; review_record_size <= review_host_size;
       ++review_record_size)
  {
    review_selection[review_record_size] =
        (unsigned)(review_value % review_expected_counts[review_record_size]);
    review_value /= review_expected_counts[review_record_size];
  }
  uint64_t review_pair_values[2] = {0, 0};
  unsigned review_read_offset = 0;
  for (unsigned review_record_size = review_host_size; review_record_size >= 2;
       --review_record_size)
  {
    uint16_t review_links[MAXN] = {0};
    unsigned review_node_mask = 0;
    for (unsigned j = 0; j < review_record_size - 1; ++j)
    {
      unsigned review_pair_index = review_check_record[review_read_offset++];
      if (review_pair_index >= review_pair_total)
        review_report_failure("E2 index=%" PRIu64 " edge=%u", review_current_selection,
                              review_pair_index);
      uint64_t review_first_bit = UINT64_C(1) << (review_pair_index & 63);
      if (review_pair_values[review_pair_index >> 6] & review_first_bit)
        review_report_failure("E1 index=%" PRIu64 " duplicate edge=%u",
                              review_current_selection, review_pair_index);
      review_pair_values[review_pair_index >> 6] |= review_first_bit;
      unsigned u = review_pair_left[review_pair_index],
               review_node_index = review_pair_right[review_pair_index];
      review_links[u] |= (uint16_t)(1u << review_node_index);
      review_links[review_node_index] |= (uint16_t)(1u << u);
      review_node_mask |= (1u << u) | (1u << review_node_index);
    }
    if (review_bit_count(review_node_mask) != review_record_size)
      review_report_failure("E3 index=%" PRIu64 " k=%u", review_current_selection,
                            review_record_size);
    if (!review_check_connected(review_links, review_node_mask))
      review_report_failure("E4 index=%" PRIu64 " k=%u", review_current_selection,
                            review_record_size);
    if (review_normalize_shape(review_links, review_node_mask) !=
        review_reference_codes[review_record_size]
                              [review_selection[review_record_size]])
      review_report_failure("E5 index=%" PRIu64 " k=%u tree=%u",
                            review_current_selection, review_record_size,
                            review_selection[review_record_size]);
  }
  if (review_read_offset != review_pair_total)
    review_report_failure("internal block accounting error");
}

static uint64_t review_parsed_number(const char *review_text_value)
{
  if (!*review_text_value || *review_text_value == '-' || *review_text_value == '+' ||
      *review_text_value == ' ')
    review_report_failure("invalid nonnegative integer");
  char *review_end_pointer;
  errno = 0;
  unsigned long long review_parsed_value =
      strtoull(review_text_value, &review_end_pointer, 10);
  if (errno || *review_end_pointer)
    review_report_failure("invalid nonnegative integer: %s", review_text_value);
  return (uint64_t)review_parsed_value;
}

int main(int review_argument_count, char **review_argument_values)
{
  uint64_t review_order_argument = 0, review_slice_index = 0, review_stride_size = 1,
           review_read_limit = 0;
  const char *review_catalogue = NULL, *review_output_record = NULL;
  unsigned review_read_options = 0;
  for (int review_position = 1; review_position < review_argument_count;
       review_position += 2)
  {
    if (review_position + 1 >= review_argument_count)
      review_report_failure("missing option value");
    unsigned review_first_bit = 0;
    if (!strcmp(review_argument_values[review_position], "--n"))
    {
      review_first_bit = 1;
      review_order_argument =
          review_parsed_number(review_argument_values[review_position + 1]);
    }
    else if (!strcmp(review_argument_values[review_position], "--trees"))
    {
      review_first_bit = 2;
      review_catalogue = review_argument_values[review_position + 1];
    }
    else if (!strcmp(review_argument_values[review_position], "--cert"))
    {
      review_first_bit = 4;
      review_output_record = review_argument_values[review_position + 1];
    }
    else if (!strcmp(review_argument_values[review_position], "--res"))
    {
      review_first_bit = 8;
      review_slice_index =
          review_parsed_number(review_argument_values[review_position + 1]);
    }
    else if (!strcmp(review_argument_values[review_position], "--mod"))
    {
      review_first_bit = 16;
      review_stride_size =
          review_parsed_number(review_argument_values[review_position + 1]);
    }
    else if (!strcmp(review_argument_values[review_position], "--limit"))
    {
      review_first_bit = 32;
      review_read_limit =
          review_parsed_number(review_argument_values[review_position + 1]);
    }
    else
      review_report_failure("unknown option: %s",
                            review_argument_values[review_position]);
    if (review_read_options & review_first_bit)
      review_report_failure("duplicate option: %s",
                            review_argument_values[review_position]);
    review_read_options |= review_first_bit;
  }
  if ((review_read_options & 7) != 7 || review_order_argument < 2 ||
      review_order_argument > MAXN || !review_stride_size ||
      (review_slice_index >= review_stride_size && !review_read_limit))
    review_report_failure(
        "usage: --n 2..12 --trees FILE --cert FILE [--res R --mod M] [--limit K]");
  unsigned review_host_size = (unsigned)review_order_argument,
           review_pair_total = review_host_size * (review_host_size - 1) / 2;
  review_read_catalogue(review_catalogue, review_host_size);
  unsigned review_pair_index = 0;
  for (unsigned u = 0; u < review_host_size; ++u)
    for (unsigned review_node_index = u + 1; review_node_index < review_host_size;
         ++review_node_index)
    {
      review_pair_left[review_pair_index] = u;
      review_pair_right[review_pair_index++] = review_node_index;
    }
  uint64_t review_sequence_count = 1;
  for (unsigned review_record_size = 2; review_record_size <= review_host_size;
       ++review_record_size)
    review_sequence_count *= review_expected_counts[review_record_size];
  uint64_t review_selection_slice =
      review_slice_index < review_sequence_count
          ? 1 + (review_sequence_count - 1 - review_slice_index) / review_stride_size
          : 0;
  if (review_read_limit > review_selection_slice)
    review_report_failure("limit exceeds slice cardinality");
  uint64_t review_expected_total =
      review_read_limit ? review_read_limit : review_selection_slice;
  FILE *review_record_file = fopen(review_output_record, "rb");
  if (!review_record_file)
    review_report_failure("cannot open certificate: %s", strerror(errno));
  struct stat review_file_info;
  if (fstat(fileno(review_record_file), &review_file_info) ||
      !S_ISREG(review_file_info.st_mode) || review_file_info.st_size < 0)
    review_report_failure("certificate must be a readable regular file");
  if ((uint64_t)review_file_info.st_size != review_expected_total * review_pair_total)
    review_report_failure("E6 bytes=%" PRIu64 " expected=%" PRIu64,
                          (uint64_t)review_file_info.st_size,
                          review_expected_total * review_pair_total);
  static char review_read_buffer[1 << 20];
  if (setvbuf(review_record_file, review_read_buffer, _IOFBF,
              sizeof review_read_buffer))
    review_report_failure("certificate buffer setup failed");
  unsigned char review_check_record[MAXEDGE];
  review_current_selection = review_slice_index;
  for (uint64_t review_position = 0; review_position < review_expected_total;
       ++review_position)
  {
    if (fread(review_check_record, 1, review_pair_total, review_record_file) !=
        review_pair_total)
      review_report_failure("E6 missing record=%" PRIu64, review_position);
    review_check_selection(review_check_record, review_host_size, review_pair_total);
    if (review_position + 1 < review_expected_total)
      review_current_selection += review_stride_size;
  }
  if (fgetc(review_record_file) != EOF || ferror(review_record_file))
    review_report_failure("E6 trailing data or read error");
  if (fclose(review_record_file))
    review_report_failure("certificate close error");
  printf("VERIFIED%s n=%u slice=%" PRIu64 "/%" PRIu64 ": %" PRIu64
         " certificates, E1-E6 hold%s\n",
         review_read_limit ? "-PREFIX" : "", review_host_size, review_slice_index,
         review_stride_size, review_expected_total,
         review_read_limit ? " for the requested prefix" : "");
  return 0;
}
