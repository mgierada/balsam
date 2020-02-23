REPR_OUTPUT_SIZE = 20


class Manager:
    model_class = None
    unique_field = None
    list_is_paginated = True

    def __init__(self, client_resource, model_class, unique_field="pk"):
        self._resource = client_resource
        self._model = model_class
        self._unique_field = unique_field

    def all(self):
        return Query(manager=self)

    def filter(self, **kwargs):
        return Query(manager=self).filter(**kwargs)

    def get(self, **kwargs):
        return Query(manager=self).get(**kwargs)

    def bulk_create(self, instances):
        """Returns a list of newly created instances"""
        if not isinstance(instances, list):
            raise TypeError(
                f"instances must be a list of {self._model.__name__} instances"
            )

        assert all(
            isinstance(obj, self._model) for obj in instances
        ), f"bulk_create requires all items to be instances of {self._model.__name__}"

        data_list = [self.to_dict(obj) for obj in instances]
        response_data = self._resource.bulk_create(data_list)
        return [self.from_dict(dat) for dat in response_data]

    def bulk_update(self, instances, update_fields):
        """
        Perform a bulk patch of instances from the modified `instances` list and set of
        `update_fields`. Modifies the instances list in-place and returns None.
        """
        data_list = [self.to_dict(obj) for obj in instances]
        patch_list = [{key: d[key] for key in update_fields} for d in data_list]
        response_data = self._resource.bulk_update_patch(patch_list)

        response_map = {item[self._unique_field]: item for item in response_data}

        # Use response_map to update instances in-place
        for i, obj in enumerate(instances):
            pk = getattr(obj, self._unique_field)
            updated_instance = self.from_dict(response_map[pk])
            instances[i] = updated_instance

        return None

    def to_dict(self, instance):
        return instance.dict()

    def from_dict(self, data):
        return self._model.construct(**data)

    def unpack_list_response(self, list_response):
        pass


class BaseIterable:
    def __init__(self, query):
        self.query = query

    def __iter__(self):
        query = self.query
        results = self._resource.list(
            query.model_class.__name__,
            {"pk", *query.model_class._field_names},
            filters=query._filters,
            excludes=query._excludes,
            order_by=query._order_fields,
            limit=query._limit,
            offset=query._offset,
        )
        query._count = results["count"]
        return self._iter_results(results["rows"])

    def _iter_results(self, results):
        raise NotImplementedError


class ModelIterable(BaseIterable):
    def _iter_results(self, results):
        for row in results:
            yield self.query.model_class(**row)


class Query:
    def __init__(self, model_class=None):
        self.model_class = model_class
        self._result_cache = None
        self._filters = {}
        self._excludes = {}
        self._order_fields = []
        self._iterable_class = ModelIterable
        self._count = None
        self._limit = None
        self._offset = None

    def __get__(self, instance, cls=None):
        if instance is not None:
            raise AttributeError(
                "Query isn't accessible via %s instances" % cls.__name__
            )

    def __repr__(self):
        data = list(self[: REPR_OUTPUT_SIZE + 1])
        if len(data) > REPR_OUTPUT_SIZE:
            data[-1] = "...(remaining elements truncated)..."
        return "<%s %r>" % (self.__class__.__name__, data)

    def __len__(self):
        self._fetch_cache()
        return len(self._result_cache)

    def __bool__(self):
        self._fetch_cache()
        return bool(self._result_cache)

    def __getitem__(self, k):
        """
        Retrieve an item or slice from the set of results.
        """
        if not isinstance(k, (int, slice)):
            raise TypeError(
                "Query indices must be integers or slices, not %s." % type(k).__name__
            )
        assert (not isinstance(k, slice) and (k >= 0)) or (
            isinstance(k, slice)
            and (k.start is None or k.start >= 0)
            and (k.stop is None or k.stop >= 0)
        ), "Negative indexing is not supported."

        if self._result_cache is not None:
            return self._result_cache[k]

        if isinstance(k, slice):
            clone = self._clone()
            if k.start is not None:
                start = int(k.start)
            else:
                start = None
            if k.stop is not None:
                stop = int(k.stop)
            else:
                stop = None
            clone._set_limits(start, stop)
            return list(clone)[:: k.step] if k.step else clone
        else:
            clone = self._clone()
            clone._set_limits(k, k + 1)
            clone._fetch_cache()
            return clone._result_cache[0]

    @property
    def is_sliced(self):
        return self._limit is None or self._offset is None

    def _clone(self):
        clone = Query(self.model_class)
        clone._filters = self._filters.copy()
        clone._excludes = self._excludes.copy()
        clone._order_fields = self._order_fields.copy()
        clone._iterable_class = self._iterable_class
        clone._limit = self._limit
        clone._offset = self._offset
        return clone

    def _set_limits(self, start, stop):
        self._offset = start
        self._limit = stop - start

    def _fetch_cache(self):
        if self._result_cache is None:
            self._result_cache = list(self._query_iterator())

    def _query_iterator(self):
        return iter(self._iterable_class(self))

    def __iter__(self):
        self._fetch_cache()
        return iter(self._result_cache)

    def filter(self, **kwargs):
        if self.is_sliced:
            return AttributeError("Cannot filter a sliced Query")
        clone = self._clone()
        clone._filters.update(kwargs)
        return clone

    def exclude(self, **kwargs):
        if self.is_sliced:
            return AttributeError("Cannot filter a sliced Query")
        clone = self._clone()
        clone._excludes.update(kwargs)
        return clone

    def order_by(self, *fields):
        if self.is_sliced:
            return AttributeError("Cannot re-order a sliced Query")
        clone = self._clone()
        clone._order_fields = tuple(fields)
        return clone

    # Methods that do not return a Query
    # **********************************
    def get(self, **kwargs):
        clone = self.filter(**kwargs)
        results = list(clone)
        nobj = len(results)
        if nobj == 1:
            return results[0]
        elif nobj == 0:
            raise self.model_class.DoesNotExist
        else:
            raise self.model_class.MultipleObjectsReturned(nobj)

    def count(self):
        if self._count is None:
            results = self._resource.list(
                self.model_class,
                filters=self._filters,
                excludes=self._excludes,
                order_by=None,
                limit=0,
                offset=self._offset,
                count_only=True,
            )
            self._count = results["count"]
        return self._count

    def update(self, **kwargs):
        pass

    def delete(self):
        pass

    def get_or_create(self, defaults=None, **kwargs):
        pass

    def update_or_create(self, defaults=None, **kwargs):
        pass

    def bulk_update(self, objs, fields):
        pass

    def bulk_create(self, objs):
        pass

    def create(self, **kwargs):
        pass

    def save(self):
        pass
