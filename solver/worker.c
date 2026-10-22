// worker

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <time.h>

#define MAXN 12
#define MAXK MAXN
#define MAXTREES 551
#define GREEDY_RESTARTS 200
#define REUSE_REPAIR_STEPS 3
#define REUSE_NODE_BUDGET 512

typedef struct
{
  unsigned char parents[MAXK];
} ShapeRecord;

static int order_size;
static ShapeRecord catalogue[MAXN + 1][MAXTREES];
static int catalogue_sizes[MAXN + 1];
static int pair_lookup[MAXN][MAXN];
static unsigned char pair_left[MAXN * MAXN], pair_right[MAXN * MAXN];
static int pair_count;
static int anchor_enabled = 1;
static int exact_required = 0;
static long long range_start, range_limit;
static uint64_t random_state;

static uint16_t links[MAXN];
static uint16_t complete_mask;

static int selection[MAXN + 1];

static unsigned char output_record[MAXN * MAXN];
static int output_length;
static unsigned char saved_record[MAXN * MAXN];
static int saved_selection[MAXN + 1], saved_ready;
static unsigned long long cached_attempts, cached_successes, repair_count,
    cached_exhaustions;
static int remaining_visits;

static unsigned long long processed_count = 0;
static unsigned long long quick_count = 0;
static unsigned long long initial_quick_count = 0;
static unsigned long long fallback_count = 0;
static unsigned long long exact_count = 0;
static unsigned long long rejected_count = 0;
static unsigned long long restart_count = 0;
static unsigned long long visited_count = 0;

static inline uint64_t next_word(void)
{
  uint64_t value = random_state;
  value ^= value << 13;
  value ^= value >> 7;
  value ^= value << 17;
  return random_state = value;
}
static inline int choose_below(int block_size)
{
  return (int)(next_word() % (uint64_t)block_size);
}

static void read_catalogue(const char *file_path)
{
  FILE *input_stream = fopen(file_path, "r");
  if (!input_stream)
  {
    fprintf(stderr, "FATAL: cannot open tree file %s\n", file_path);
    exit(2);
  }
  char input_line[512];
  int active_size = 0;
  while (fgets(input_line, sizeof input_line, input_stream))
  {
    if (input_line[0] == '#')
    {
      if (sscanf(input_line, "# %d", &active_size) != 1)
      {
        fprintf(stderr, "FATAL: bad header %s", input_line);
        exit(2);
      }
      if (active_size < 1 || active_size > MAXN)
      {
        fprintf(stderr, "FATAL: k=%d out of range\n", active_size);
        exit(2);
      }
      continue;
    }
    if (input_line[0] == '\n' || input_line[0] == '\0')
      continue;
    if (active_size == 0)
    {
      fprintf(stderr, "FATAL: tree data before any header\n");
      exit(2);
    }
    int parsed_values[MAXK + 1], item_count = 0;
    char *parent_index = input_line;
    while (item_count < MAXK + 1)
    {
      char *end_pointer;
      long node_index = strtol(parent_index, &end_pointer, 10);
      if (end_pointer == parent_index)
        break;
      parsed_values[item_count++] = (int)node_index;
      parent_index = end_pointer;
    }
    if (item_count != active_size)
    {
      fprintf(stderr, "FATAL: k=%d line has %d entries\n", active_size, item_count);
      exit(2);
    }
    if (parsed_values[0] != 0)
    {
      fprintf(stderr, "FATAL: k=%d root parent is %d not 0\n", active_size,
              parsed_values[0]);
      exit(2);
    }
    if (catalogue_sizes[active_size] >= MAXTREES)
    {
      fprintf(stderr, "FATAL: too many trees at k=%d\n", active_size);
      exit(2);
    }
    ShapeRecord *shape_record = &catalogue[active_size][catalogue_sizes[active_size]++];
    shape_record->parents[0] = 0;
    for (int position = 1; position < active_size; position++)
    {
      int root_parents = parsed_values[position] - 1;
      if (root_parents < 0 || root_parents >= position)
      {
        fprintf(stderr, "FATAL: k=%d vertex %d parent %d not < %d\n", active_size,
                position, root_parents, position);
        exit(2);
      }
      shape_record->parents[position] = (unsigned char)root_parents;
    }
  }
  fclose(input_stream);
}

static const int catalogue_counts[] = {0, 1, 1, 1, 2, 3, 6, 11,
                                       23, 47, 106, 235, 551};
static void check_catalogue_sizes(void)
{
  for (int record_size = 2; record_size <= order_size; record_size++)
  {
    if (catalogue_sizes[record_size] != catalogue_counts[record_size])
    {
      fprintf(stderr, "FATAL: k=%d has %d trees, A000055 says %d\n", record_size,
              catalogue_sizes[record_size], catalogue_counts[record_size]);
      exit(2);
    }
  }
}

static uint64_t choice_rows[1u << MAXN];
static int placement_size;
static const unsigned char *parent_rows;
static unsigned char shape_children[MAXN + 1][MAXTREES][MAXK];
static const unsigned char *child_rows;
static int placement[MAXK];
static uint16_t placement_mask;

static int place_next(int position)
{
  if (position == placement_size)
    return 1;
  uint16_t choices =
      (position == 0)
          ? (uint16_t)(complete_mask & ~placement_mask)
          : (uint16_t)(links[placement[parent_rows[position]]] & ~placement_mask);
  if (!choices)
    return 0;
  uint64_t packed_choices = choice_rows[choices];
  int neighbor_index = (int)(packed_choices >> 56);
  while (neighbor_index)
  {
    int first_value = neighbor_index > 1 ? choose_below(neighbor_index) : 0;
    int bit_offset = 4 * first_value;
    int node_index = (int)((packed_choices >> bit_offset) & 15);
    neighbor_index--;
    uint64_t last_index = (packed_choices >> (4 * neighbor_index)) & 15;
    packed_choices =
        (packed_choices & ~(UINT64_C(15) << bit_offset)) | (last_index << bit_offset);
    if (__builtin_popcount((unsigned)(links[node_index] & ~placement_mask)) <
        child_rows[position])
      continue;
    placement[position] = node_index;
    placement_mask |= (uint16_t)(1u << node_index);
    if (place_next(position + 1))
      return 1;
    placement_mask &= (uint16_t) ~(1u << node_index);
  }
  return 0;
}

static int place_cached(int position)
{
  if (position == placement_size)
    return 1;
  if (!remaining_visits)
    return 0;
  remaining_visits--;
  uint16_t choices =
      (position == 0)
          ? (uint16_t)(complete_mask & ~placement_mask)
          : (uint16_t)(links[placement[parent_rows[position]]] & ~placement_mask);
  if (!choices)
    return 0;
  uint64_t packed_choices = choice_rows[choices];
  int neighbor_index = (int)(packed_choices >> 56);
  while (neighbor_index)
  {
    if (!remaining_visits)
      return 0;
    int first_value = neighbor_index > 1 ? choose_below(neighbor_index) : 0;
    int bit_offset = 4 * first_value;
    int node_index = (int)((packed_choices >> bit_offset) & 15);
    neighbor_index--;
    uint64_t last_index = (packed_choices >> (4 * neighbor_index)) & 15;
    packed_choices =
        (packed_choices & ~(UINT64_C(15) << bit_offset)) | (last_index << bit_offset);
    if (__builtin_popcount((unsigned)(links[node_index] & ~placement_mask)) <
        child_rows[position])
      continue;
    placement[position] = node_index;
    placement_mask |= (uint16_t)(1u << node_index);
    if (place_cached(position + 1))
      return 1;
    placement_mask &= (uint16_t) ~(1u << node_index);
  }
  return 0;
}

static void remove_placement(void)
{
  for (int position = 1; position < placement_size; position++)
  {
    int u = placement[parent_rows[position]], node_index = placement[position];
    links[u] &= (uint16_t) ~(1u << node_index);
    links[node_index] &= (uint16_t) ~(1u << u);
  }
}
static void restore_placement(int locations[], const unsigned char *parents,
                              int record_size)
{
  for (int position = 1; position < record_size; position++)
  {
    int u = locations[parents[position]], node_index = locations[position];
    links[u] |= (uint16_t)(1u << node_index);
    links[node_index] |= (uint16_t)(1u << u);
  }
}

static void reset_available(void)
{
  for (int node_index = 0; node_index < order_size; node_index++)
    links[node_index] = (uint16_t)(complete_mask & ~(1u << node_index));

}

static void place_anchor(const ShapeRecord *shape_record)
{
  for (int position = 1; position < order_size; position++)
  {
    int u = shape_record->parents[position], node_index = position;
    links[u] &= (uint16_t) ~(1u << node_index);
    links[node_index] &= (uint16_t) ~(1u << u);
    output_record[output_length++] = (unsigned char)pair_lookup[u][node_index];
  }
}

static int try_quick(void)
{
  reset_available();
  output_length = 0;
  int start_index = order_size;
  if (anchor_enabled)
  {
    place_anchor(&catalogue[order_size][selection[order_size]]);
    start_index = order_size - 1;
  }
  for (int record_size = start_index; record_size >= 2; record_size--)
  {
    const ShapeRecord *shape_record = &catalogue[record_size][selection[record_size]];
    placement_size = record_size;
    parent_rows = shape_record->parents;
    placement_mask = 0;
    child_rows = shape_children[record_size][selection[record_size]];
    if (!place_next(0))
      return 0;
    remove_placement();
    for (int position = 1; position < placement_size; position++)
      output_record[output_length++] = (unsigned char)pair_lookup
          [placement[parent_rows[position]]][placement[position]];
  }
  return 1;
}

static int repair_tail(int start_index, int retained_count)
{
  memset(links, 0, sizeof links);
  memcpy(output_record, saved_record, (size_t)retained_count);
  output_length = retained_count;
  for (int position = retained_count; position < pair_count; position++)
  {
    int pair_index = saved_record[position], u = pair_left[pair_index],
        node_index = pair_right[pair_index];
    links[u] |= (uint16_t)(1u << node_index);
    links[node_index] |= (uint16_t)(1u << u);
  }
  for (int record_size = start_index; record_size >= 2; record_size--)
  {
    const ShapeRecord *shape_record = &catalogue[record_size][selection[record_size]];
    placement_size = record_size;
    parent_rows = shape_record->parents;
    placement_mask = 0;
    child_rows = shape_children[record_size][selection[record_size]];
    if (!place_cached(0))
      return 0;
    remove_placement();
    for (int position = 1; position < placement_size; position++)
      output_record[output_length++] = (unsigned char)pair_lookup
          [placement[parent_rows[position]]][placement[position]];
  }
  return 1;
}

static int try_cached(void)
{
  if (!saved_ready)
    return 0;
  int start_index = order_size, retained_count = 0;
  while (start_index >= 2 && selection[start_index] == saved_selection[start_index])
  {
    retained_count += start_index - 1;
    start_index--;
  }
  int retained_length = anchor_enabled ? order_size - 1 : 0;
  remaining_visits = REUSE_NODE_BUDGET;
  for (int step_index = 0;
       step_index <= REUSE_REPAIR_STEPS && retained_count > retained_length;
       step_index++)
  {
    cached_attempts++;
    if (repair_tail(start_index, retained_count))
    {
      cached_successes++;
      if (step_index)
        repair_count++;
      return 1;
    }
    if (!remaining_visits)
    {
      cached_exhaustions++;
      break;
    }
    retained_count -= start_index;
    start_index++;
  }
  return 0;
}

static int search_placement[MAXN + 1][MAXK];

static int solve_remaining(int search_depth);

static int place_exact(int search_depth, int position, uint16_t occupied_mask)
{
  const ShapeRecord *shape_record = &catalogue[search_depth][selection[search_depth]];
  if (position == search_depth)
  {
    for (int inner_position = 1; inner_position < search_depth;
         inner_position++)
    {
      int u = search_placement[search_depth][shape_record->parents[inner_position]],
          node_index = search_placement[search_depth][inner_position];
      links[u] &= (uint16_t) ~(1u << node_index);
      links[node_index] &= (uint16_t) ~(1u << u);
    }
    int accepted = solve_remaining(search_depth - 1);
    if (!accepted)
      restore_placement(search_placement[search_depth], shape_record->parents,
                        search_depth);
    return accepted;
  }
  uint16_t choices =
      (position == 0)
          ? (uint16_t)(complete_mask & ~occupied_mask)
          : (uint16_t)(links[search_placement[search_depth]
                                             [shape_record->parents[position]]] &
                       ~occupied_mask);
  for (int node_index = 0; node_index < order_size; node_index++)
  {
    if (!(choices & (1u << node_index)))
      continue;
    visited_count++;
    search_placement[search_depth][position] = node_index;
    if (place_exact(search_depth, position + 1,
                    (uint16_t)(occupied_mask | (1u << node_index))))
      return 1;
  }
  return 0;
}

static int solve_remaining(int search_depth)
{
  if (search_depth == 1)
  {
    for (int node_index = 0; node_index < order_size; node_index++)
      if (links[node_index])
        return 0;
    return 1;
  }
  return place_exact(search_depth, 0, 0);
}

static int run_complete_search(void)
{
  reset_available();
  output_length = 0;
  int start_index = order_size;
  if (anchor_enabled)
  {
    place_anchor(&catalogue[order_size][selection[order_size]]);
    start_index = order_size - 1;
  }
  if (!solve_remaining(start_index))
    return 0;
  for (int record_size = start_index; record_size >= 2; record_size--)
  {
    const ShapeRecord *shape_record = &catalogue[record_size][selection[record_size]];
    for (int inner_position = 1; inner_position < record_size;
         inner_position++)
      output_record[output_length++] = (unsigned char)pair_lookup
          [search_placement[record_size][shape_record->parents[inner_position]]]
          [search_placement[record_size][inner_position]];
  }
  return 1;
}

static FILE *output_stream = NULL, *rejected_stream = NULL;

static void describe_selection(char *text_buffer, size_t buffer_size)
{
  size_t write_offset = 0;
  for (int record_size = 2; record_size <= order_size; record_size++)
    write_offset +=
        snprintf(text_buffer + write_offset, buffer_size - write_offset, "%s%d",
                 record_size == 2 ? "" : ",", selection[record_size]);
}

static void process_selection(void)
{
  processed_count++;
  int completed = 0, retry_count = 0;
  if (!exact_required)
  {
    completed = try_cached();
    if (!completed)
    {
      for (retry_count = 0; retry_count <= GREEDY_RESTARTS; retry_count++)
      {
        if (try_quick())
        {
          completed = 1;
          break;
        }
      }
    }
  }
  if (completed)
  {
    quick_count++;
    restart_count += (unsigned long long)retry_count;
    if (retry_count == 0)
      initial_quick_count++;
  }
  else
  {

    fallback_count++;
    if (run_complete_search())
    {
      exact_count++;
    }
    else
    {
      rejected_count++;
      char text_buffer[256];
      describe_selection(text_buffer, sizeof text_buffer);
      fprintf(stderr, "STAGE-B-UNSAT seq=%s\n", text_buffer);
      if (rejected_stream)
      {
        fprintf(rejected_stream, "%s\n", text_buffer);
        fflush(rejected_stream);
      }
      return;
    }
  }
  if (output_length != pair_count)
  {
    char text_buffer[256];
    describe_selection(text_buffer, sizeof text_buffer);
    fprintf(stderr, "FATAL: cert_len=%d != %d for seq=%s\n", output_length, pair_count,
            text_buffer);
    exit(3);
  }
  memcpy(saved_record, output_record, (size_t)pair_count);
  memcpy(saved_selection, selection, sizeof selection);
  saved_ready = 1;
  if (output_stream)
    fwrite(output_record, 1, (size_t)pair_count, output_stream);
}

static void run_range(long long slice_index, long long stride_size)
{
  long long sequence_count = 1;
  for (int record_size = 2; record_size <= order_size; record_size++)
    sequence_count *= catalogue_sizes[record_size];
  long long first_index = slice_index, written_count = 0;
  if (range_start > first_index)
    first_index +=
        ((range_start - first_index + stride_size - 1) / stride_size) * stride_size;
  if (range_start || range_limit)
    fprintf(stderr, "SELECTION start=%lld limit=%lld first=%lld\n", range_start,
            range_limit, first_index);
  for (long long selection_index = first_index;
       selection_index < sequence_count &&
       (!range_limit || written_count < range_limit);
       selection_index += stride_size)
  {
    long long value = selection_index;
    for (int record_size = 2; record_size <= order_size; record_size++)
    {
      selection[record_size] = (int)(value % catalogue_sizes[record_size]);
      value /= catalogue_sizes[record_size];
    }
    process_selection();
    written_count++;
  }
}

int main(int argument_count, char **argument_values)
{
  const char *catalogue_path = "input.txt", *output_path = NULL, *rejected_path = NULL;
  long long slice_index = 0, stride_size = 1;
  uint64_t initial_state = 20260730ULL;
  order_size = 0;
  for (int position = 1; position < argument_count; position++)
  {
    if (!strcmp(argument_values[position], "--n"))
      order_size = atoi(argument_values[++position]);
    else if (!strcmp(argument_values[position], "--trees"))
      catalogue_path = argument_values[++position];
    else if (!strcmp(argument_values[position], "--cert"))
      output_path = argument_values[++position];
    else if (!strcmp(argument_values[position], "--unsat"))
      rejected_path = argument_values[++position];
    else if (!strcmp(argument_values[position], "--res"))
      slice_index = atoll(argument_values[++position]);
    else if (!strcmp(argument_values[position], "--mod"))
      stride_size = atoll(argument_values[++position]);
    else if (!strcmp(argument_values[position], "--start"))
      range_start = atoll(argument_values[++position]);
    else if (!strcmp(argument_values[position], "--limit"))
      range_limit = atoll(argument_values[++position]);
    else if (!strcmp(argument_values[position], "--seed"))
      initial_state = strtoull(argument_values[++position], NULL, 10);
    else if (!strcmp(argument_values[position], "--no-wlog"))
      anchor_enabled = 0;
    else if (!strcmp(argument_values[position], "--force-exact"))
      exact_required = 1;
    else
    {
      fprintf(stderr, "unknown arg %s\n", argument_values[position]);
      return 2;
    }
  }
  if (order_size < 2 || order_size > MAXN)
  {
    fprintf(
        stderr,
        "usage: worker --n N [--trees f] [--cert f] [--unsat f] [--res r --mod m]\n"
        "            [--seed s] [--no-wlog] [--force-exact]\n");
    return 2;
  }
  if (range_start < 0 || range_limit < 0 || stride_size <= 0 || slice_index < 0 ||
      slice_index >= stride_size)
  {
    fprintf(stderr, "FATAL: invalid sweep selection\n");
    return 2;
  }
  random_state = initial_state ? initial_state : 1;

  read_catalogue(catalogue_path);
  check_catalogue_sizes();
  for (int record_size = 2; record_size <= order_size; record_size++)
    for (int shape_record = 0; shape_record < catalogue_sizes[record_size];
         shape_record++)
      for (int position = 1; position < record_size; position++)
        shape_children[record_size][shape_record]
                      [catalogue[record_size][shape_record].parents[position]]++;

  pair_count = 0;
  for (int u = 0; u < order_size; u++)
    for (int node_index = u + 1; node_index < order_size; node_index++)
    {
      pair_left[pair_count] = (unsigned char)u;
      pair_right[pair_count] = (unsigned char)node_index;
      pair_lookup[u][node_index] = pair_lookup[node_index][u] = pair_count++;
    }
  if (pair_count != order_size * (order_size - 1) / 2)
  {
    fprintf(stderr, "FATAL: edge count\n");
    return 3;
  }

  int total_sum = 0;
  for (int record_size = 2; record_size <= order_size; record_size++)
    total_sum += record_size - 1;
  if (total_sum != pair_count)
  {
    fprintf(stderr, "FATAL: sum(k-1)=%d != C(n,2)=%d\n", total_sum, pair_count);
    return 3;
  }
  complete_mask = (uint16_t)((1u << order_size) - 1);
  for (unsigned selected_mask = 1; selected_mask <= complete_mask; selected_mask++)
  {
    uint64_t packed_choices = 0;
    unsigned remaining_mask = selected_mask;
    int match_count = 0;
    while (remaining_mask)
    {
      unsigned selected_node = (unsigned)__builtin_ctz(remaining_mask);
      packed_choices |= (uint64_t)selected_node << (4 * match_count++);
      remaining_mask &= remaining_mask - 1;
    }
    choice_rows[selected_mask] = packed_choices | ((uint64_t)match_count << 56);
  }

  if (output_path)
  {
    output_stream = fopen(output_path, "wb");
    if (!output_stream)
    {
      perror("cert");
      return 2;
    }
  }
  if (rejected_path)
  {
    rejected_stream = fopen(rejected_path, "w");
    if (!rejected_stream)
    {
      perror("unsat");
      return 2;
    }
  }

  long long sequence_count = 1;
  for (int record_size = 2; record_size <= order_size; record_size++)
    sequence_count *= catalogue_sizes[record_size];
  fprintf(stderr, "worker: n=%d trees/size:", order_size);
  for (int record_size = 2; record_size <= order_size; record_size++)
    fprintf(stderr, " %d", catalogue_sizes[record_size]);
  fprintf(stderr,
          "  total_sequences=%lld  edges=%d  wlog=%d  slice=%lld/%lld  seed=%llu\n",
          sequence_count, pair_count, anchor_enabled, slice_index, stride_size,
          (unsigned long long)initial_state);

  clock_t start_clock = clock();
  run_range(slice_index, stride_size);
  double elapsed_seconds = (double)(clock() - start_clock) / CLOCKS_PER_SEC;
  if (output_stream)
    fclose(output_stream);
  if (rejected_stream)
    fclose(rejected_stream);

  printf(
      "LEDGER n=%d res=%lld mod=%lld seed=%llu wlog=%d total=%llu stageA=%llu stageA_first_try=%llu "
      "escalated=%llu stageB=%llu UNSAT=%llu greedy_restarts=%llu exact_nodes=%llu secs=%.2f\n",
      order_size, slice_index, stride_size, (unsigned long long)initial_state,
      anchor_enabled, processed_count, quick_count, initial_quick_count, fallback_count,
      exact_count, rejected_count, restart_count, visited_count, elapsed_seconds);

  if (quick_count + exact_count + rejected_count != processed_count)
  {
    fprintf(stderr, "FATAL: accounting mismatch %llu+%llu+%llu != %llu\n", quick_count,
            exact_count, rejected_count, processed_count);
    return 3;
  }
  fprintf(stderr, "REUSE attempts=%llu successes=%llu repairs=%llu exhausted=%llu\n",
          cached_attempts, cached_successes, repair_count, cached_exhaustions);
  return rejected_count ? 1 : 0;
}
