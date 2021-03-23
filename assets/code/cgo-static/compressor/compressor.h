#include <stdlib.h>

#ifdef __cplusplus
extern "C" {
#endif
  typedef int ErrorCode;

  typedef struct result_t {
      char * output;
      unsigned long int length;
      ErrorCode errorCode;
  } result_t;

  struct result_t* compressData(char *input);
#ifdef __cplusplus
}
#endif
