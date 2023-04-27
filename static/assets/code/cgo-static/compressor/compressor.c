#include <string.h> 
#include "zlib.h"
#include "compressor.h"

struct result_t* compressData(char* input)
{   
    struct result_t *result = (struct result_t *) malloc(sizeof(result_t));

    unsigned long int ucompSize = strlen(input); 
    unsigned long int compSizeBound = compressBound(ucompSize);

    char *output = (char *) malloc(compSizeBound);

    int ok = compress((Bytef *)output, &compSizeBound, (Bytef *)input, ucompSize);
    if (ok != Z_OK) {
        result->errorCode = ok;
        return result;
    }

    result->errorCode = ok;
    result->output = output;
    result->length = compSizeBound;

    return result;
}