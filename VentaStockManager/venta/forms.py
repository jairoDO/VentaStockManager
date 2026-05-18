from dal import autocomplete
from django import forms

from django.contrib import admin
from django.contrib.admin.widgets import RelatedFieldWidgetWrapper
from django.urls import reverse
from venta.models import ArticuloVenta
from .models import Pedido, Venta
from  cliente.models import Cliente
from django.urls import reverse
from django.forms.models import BaseInlineFormSet


from django import forms
import logging

class VentaForm(forms.ModelForm):
    class Meta:
        model = Venta
        fields = '__all__'
        widgets = {
            'cliente': autocomplete.ModelSelect2(url='cliente-autocomplete',
                        attrs={'data-placeholder': 'Buscar Cliente', 'empty_label': 'No_seleccionado'})
,
        }
    def __init__(self, *args, **kwargs):        
        from VentaStockManager.admin import admin_site  # Local import

        super(VentaForm, self).__init__(*args, **kwargs)
        self.fields['cliente'].widget = RelatedFieldWidgetWrapper(
            self.fields['cliente'].widget,
            Venta._meta.get_field('cliente').remote_field,
            admin_site=admin_site,
            can_add_related=True,
            can_change_related=True,
            can_delete_related=True
        )   

    # def __init__(self, *args, **kwargs):
    #     super().__init__(*args, **kwargs)
    #     if 'cliente' in self.fields:
    #         cliente_field = self.fields['cliente']
    #         cliente_field.widget = forms.widgets.Select(attrs={'style': 'width: 80%;'})
    #         cliente_field.widget.can_add_related = True
    #         cliente_field.widget.attrs['data-add-object-url'] = reverse('admin:cliente_cliente_add')

    # NOTA: removidas las referencias a select2 desde CDN externo y a
    # init_select2.js (este ultimo no existia y daba 404). Django-autocomplete-light
    # ya trae select2 incluido y lo inicializa automaticamente.
    class Media:
        js = ()
        css = {}

class ArticuloVentaInlineFormSet(BaseInlineFormSet):
    def clean(self):
        """
        Validate the formset as a whole.
        """
        # No realizar validaciones para formularios marcados para eliminar
        for form in self.forms:
            if not hasattr(form, 'cleaned_data'):
                continue
            if form.cleaned_data.get('DELETE'):
                continue
        return super().clean()
    
    
    def save_new(self, form, commit=True):
        """¬
        Save and return a new model instance for the given form.
        """
        if not hasattr(form, 'cleaned_data'):
            return None
            
        if form.cleaned_data.get('DELETE'):
            return None
            
        # Check if the form has any actual data
        has_data = any(
            form.cleaned_data.get(field) 
            for field in ['articulo', 'cantidad', 'precio']
        )
        
        if not has_data:
            return None
            
        instance = super().save_new(form, commit=False)
        if commit and instance:
            instance.save()
        return instance

    def save(self, commit=True):
        """
        Save model instances for every form, adding and changing instances
        as necessary, and return the list of instances.
        """
        if not hasattr(self, 'cleaned_data'):
            return []

        # Reset tracking lists
        self.deleted_objects = []
        self.saved_forms = []
        self.changed_objects = []
        self.new_objects = []
        
        instances = []
        
        # Handle deletions and changes
        for form in self.forms:
            if not hasattr(form, 'cleaned_data'):
                continue
                
            if self.can_delete and form.cleaned_data.get('DELETE'):
                if form.instance.pk:
                    self.deleted_objects.append(form.instance)
                    if commit:
                        form.instance.delete()
                continue
            
            if form.has_changed():
                if form.instance.pk is None:
                    # New instance
                    instance = self.save_new(form, commit=commit)
                    if instance:
                        self.new_objects.append(instance)
                        instances.append(instance)
                else:
                    # Changed instance
                    instance = form.save(commit=commit)
                    if instance:
                        self.changed_objects.append((instance, form.changed_data))
                        instances.append(instance)
                
                if instance:
                    self.saved_forms.append(form)

        return instances

class ArticuloVentaForm(forms.ModelForm):
    class Meta:
        model = ArticuloVenta
        fields = '__all__'
        widgets = {
            'articulo': autocomplete.ModelSelect2(url='articulo-autocomplete',
                        attrs={'data-placeholder': 'Buscar Articulo', 'empty_label': 'No seleccionado'})
        }

    def __init__(self, *args, **kwargs):
        super(ArticuloVentaForm, self).__init__(*args, **kwargs)
        from VentaStockManager.admin import admin_site  # Local import
        if 'articulo' in self.fields:
            self.fields['articulo'].widget = RelatedFieldWidgetWrapper(
                self.fields['articulo'].widget,
                ArticuloVenta._meta.get_field('articulo').remote_field,
                admin_site=admin_site,
                can_add_related=True,
                can_change_related=True,
                can_delete_related=True
            )
        logging.debug(f"Initialized ArticuloVentaForm with fields: {self.fields}")

    def clean(self):
        cleaned_data = super().clean()
        
        # Solo validar si hay datos en el formulario
        if any(self.data.get(f'{self.prefix}-{field}') for field in ['articulo', 'cantidad', 'precio']):
            # Validar campos requeridos
            for field in ['articulo', 'cantidad', 'precio']:
                if not cleaned_data.get(field):
                    self.add_error(field, 'Este campo es requerido.')
            
            # Validar cantidad positiva
            if cleaned_data.get('cantidad') is not None and cleaned_data['cantidad'] <= 0:
                self.add_error('cantidad', 'La cantidad debe ser mayor que 0')
        
        return cleaned_data

    def is_valid(self):
        # Verificar si el formulario está vacío
        is_empty = not any(self.data.get(f'{self.prefix}-{field}') 
                          for field in ['articulo', 'cantidad', 'precio'])
        
        if is_empty:
            return True
        
        return super().is_valid()

    def save(self, commit=True):
        # Verificar si hay datos para guardar
        has_data = any(self.data.get(f'{self.prefix}-{field}') 
                      for field in ['articulo', 'cantidad', 'precio'])
        
        if not has_data:
            return None

        try:
            instance = super().save(commit=False)
            if commit:
                instance.save()
                logging.info(f"Guardado exitoso de ArticuloVenta: {instance.pk}")
            return instance
        except Exception as e:
            logging.error(f"Error al guardar ArticuloVenta: {str(e)}")
            raise

class PedidoEstadoForm(forms.ModelForm):
    class Meta:
        model = Pedido
        fields = ['pagado', 'estado']
        labels = {
            'pagado': 'Pagado',
            'estado': 'Estado',
        }
        widgets = {
            'pagado': forms.CheckboxInput(),
            'estado': forms.Select(choices=Pedido.ESTADO_CHOICES),
        }
